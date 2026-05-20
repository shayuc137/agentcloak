"""Shared sync + async HTTP client for the agentcloak daemon.

Why this module exists
----------------------
Before T3, daemon access was duplicated across two clients:

* ``agentcloak.cli.client.DaemonClient`` — ~30 typed wrappers with 15
  ``_run(asyncio.run(...))`` helpers scattered across CLI command files.
* ``agentcloak.mcp.client.DaemonBridge`` — generic ``request()``, its own
  subprocess spawn for auto-start (with a different flag set).

Both did the same thing with different libraries and different error
philosophies (one raised exceptions, the other returned error dicts). Adding
a route meant updating both, and they drifted.

This module exposes a single :class:`DaemonClient` with:

* **sync API** — ``client.navigate_sync(...)`` for CLI commands. Internally
  uses :class:`httpx.Client`. No ``asyncio.run`` at the call site.
* **async API** — ``await client.navigate(...)`` for MCP tools. Internally
  uses :class:`httpx.AsyncClient`.
* **one auto-start path** — ``_ensure_daemon_*`` plus ``_spawn_daemon`` are
  defined once each. Subprocess flag forwarding lives in
  :meth:`DaemonClient._build_daemon_argv`.
* **one error model** — every failure raises an :class:`AgentBrowserError`
  subclass with the standard three-field envelope. The MCP adapter is the
  layer that turns those exceptions back into JSON strings; the daemon
  client itself never returns error dicts.

Response envelope contract
--------------------------
The daemon wraps every successful response in
``{"ok": true, "seq": N, "data": <payload>}``. ``DaemonClient`` parses the
full envelope and returns it as a ``dict`` — both ``seq`` and ``data`` stay
intact, so callers can decide how much of the wrapper they care about.

The two surfaces unwrap it differently and that's intentional:

* **CLI** keeps the envelope. The :mod:`agentcloak.cli.output` helper writes
  ``{"ok": true, "seq": N, "data": ...}`` to stdout because the CLI output
  contract (``.trellis/spec/cli/cli-output-contract.md``) promises ``seq``
  on every success — agents and scripts using ``jq`` depend on it for the
  ``--since`` filter and replay logic.

* **MCP** flattens to the payload only. :func:`agentcloak.mcp._format.format_envelope`
  emits ``data`` without the wrapper because the MCP framework already
  produces a tool-call envelope around the response. Sending an additional
  ``{ok, seq, data}`` layer would double-wrap and waste tokens.

Both behaviours come from the same daemon shape — only the adapter layer
chooses what to keep. If you need ``seq`` from MCP code, read it off the
envelope before calling ``format_envelope``.

Network failure classification (A3 from the v0.2.0 plan)
--------------------------------------------------------
Different transport errors get distinct ``error`` codes so agents can react
without parsing free-form messages:

* :class:`httpx.ConnectError` → ``daemon_unreachable``
  (after auto-start has been attempted, with a clear ``action`` hint).
* :class:`httpx.ConnectTimeout` → ``daemon_connect_timeout``.
* :class:`httpx.ReadTimeout` / :class:`httpx.WriteTimeout` → ``daemon_timeout``.
* :class:`httpx.NetworkError` (other) → ``daemon_network_error``.
* :class:`httpx.RequestError` (catch-all) → ``daemon_request_failed``.

The raw Python exception is preserved via ``raise ... from exc`` for users
who attach debuggers, but agents only see the structured envelope.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from typing import Any

import httpx
import orjson
import structlog

from agentcloak.core.config import AgentcloakConfig, load_config
from agentcloak.core.errors import (
    AgentBrowserError,
    BrowserTimeoutError,
    DaemonConnectionError,
)

__all__ = ["DaemonClient"]

logger = structlog.get_logger()

# Health-probe timeout is intentionally tiny — we don't want CLI/MCP to hang
# while polling a starting daemon. The other budgets (request timeout, startup
# budget, poll interval) live on AgentcloakConfig so users can tune them.
_HEALTH_PROBE_TIMEOUT_S = 2.0


class DaemonClient:
    """HTTP client wrapping the agentcloak daemon API.

    Surface layout
    --------------
    * :meth:`_send_sync` / :meth:`_send_async` — generic dispatch over the
      daemon HTTP API. The CLI uses ``_send_sync`` directly (via
      :mod:`agentcloak.cli._dispatch`) for the majority of commands and
      renders JSON locally, so most routes don't need a typed sync wrapper.
    * Typed ``*_sync`` methods — only kept for the handful of CLI commands
      that reshape the response before rendering (base64 decode for
      screenshots, custom file output for capture export, etc.). The full
      list lives in :data:`scripts.generate_client.KEEP_SYNC_METHODS`;
      preflight will fail loudly if it falls out of sync.
    * Typed async methods (``navigate``, ``snapshot``, ...) — the canonical
      typed surface used by MCP tools and tests. Every daemon route has one.

    Both code paths share the same auto-start logic, the same envelope
    parsing, and the same exception hierarchy.

    Parameters
    ----------
    host:
        Daemon bind host. ``None`` reads ``cfg.daemon.host`` (env
        ``AGENTCLOAK_HOST`` or config file).
    port:
        Daemon bind port. ``None`` reads ``cfg.daemon.port``.
    auto_start:
        When ``True`` (default), the first request after a
        ``daemon_unreachable`` error spawns the daemon as a background
        subprocess and retries once. Pass ``False`` from commands that
        explicitly want to probe (``doctor``, ``daemon status``, etc.).
    """

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        auto_start: bool = True,
    ) -> None:
        _, cfg = load_config()
        self._cfg: AgentcloakConfig = cfg
        self._host = host or cfg.daemon.host
        self._port = port or cfg.daemon.port
        self._base = f"http://{self._host}:{self._port}"
        self._auto_start = auto_start
        # Once we have spawned the daemon (or detected one was reachable), we
        # don't repeatedly retry the spawn within a single client lifetime —
        # otherwise a tight loop of failing requests would fork many daemons.
        self._auto_started = False
        # Per-instance copies so users can tweak them on the fly (or via env)
        # without restarting the process.
        self._request_timeout_s = float(cfg.daemon.http_client_timeout)
        self._startup_budget_s = float(cfg.daemon.auto_start_timeout)
        self._poll_interval_s = float(cfg.daemon.auto_start_poll_interval)

    @property
    def config(self) -> AgentcloakConfig:
        """Snapshot of the AgentcloakConfig captured at client construction.

        Public accessor for adapters (MCP tool registration, etc.) that need
        to read default values without poking at private state.
        """
        return self._cfg

    # ------------------------------------------------------------------
    # Core request execution
    # ------------------------------------------------------------------

    def _send_sync(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            return self._do_request_sync(
                method, path, json_body=json_body, params=params
            )
        except httpx.ConnectError as exc:
            return self._handle_connect_error_sync(
                exc, method, path, json_body=json_body, params=params
            )
        except httpx.ConnectTimeout as exc:
            raise DaemonConnectionError(
                error="daemon_connect_timeout",
                hint=(
                    f"Connection to daemon at {self._host}:{self._port} "
                    "timed out before a TCP handshake completed."
                ),
                action=(
                    "check daemon status with 'agentcloak daemon status' or restart it"
                ),
            ) from exc
        except httpx.TimeoutException as exc:
            raise BrowserTimeoutError(
                error="daemon_timeout",
                hint=(
                    f"Request to {path} took longer than {self._request_timeout_s}s."
                ),
                action="retry, or increase the daemon request timeout",
            ) from exc
        except httpx.NetworkError as exc:
            raise DaemonConnectionError(
                error="daemon_network_error",
                hint=f"Network error talking to daemon: {exc}",
                action="check the daemon process and local network",
            ) from exc
        except httpx.RequestError as exc:
            raise AgentBrowserError(
                error="daemon_request_failed",
                hint=f"HTTP request to daemon failed: {exc}",
                action="check daemon status with 'agentcloak daemon status'",
            ) from exc

    async def _send_async(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            return await self._do_request_async(
                method, path, json_body=json_body, params=params
            )
        except httpx.ConnectError as exc:
            return await self._handle_connect_error_async(
                exc, method, path, json_body=json_body, params=params
            )
        except httpx.ConnectTimeout as exc:
            raise DaemonConnectionError(
                error="daemon_connect_timeout",
                hint=(
                    f"Connection to daemon at {self._host}:{self._port} "
                    "timed out before a TCP handshake completed."
                ),
                action=(
                    "check daemon status with 'agentcloak daemon status' or restart it"
                ),
            ) from exc
        except httpx.TimeoutException as exc:
            raise BrowserTimeoutError(
                error="daemon_timeout",
                hint=(
                    f"Request to {path} took longer than {self._request_timeout_s}s."
                ),
                action="retry, or increase the daemon request timeout",
            ) from exc
        except httpx.NetworkError as exc:
            raise DaemonConnectionError(
                error="daemon_network_error",
                hint=f"Network error talking to daemon: {exc}",
                action="check the daemon process and local network",
            ) from exc
        except httpx.RequestError as exc:
            raise AgentBrowserError(
                error="daemon_request_failed",
                hint=f"HTTP request to daemon failed: {exc}",
                action="check daemon status with 'agentcloak daemon status'",
            ) from exc

    def _do_request_sync(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        with httpx.Client(
            base_url=self._base, timeout=self._request_timeout_s
        ) as client:
            kwargs: dict[str, Any] = {}
            headers: dict[str, str] = {}
            if json_body is not None:
                # Use orjson for body serialization to match the rest of the
                # codebase (uniform JSON library == one ser/de behavior).
                kwargs["content"] = orjson.dumps(json_body)
                headers["Content-Type"] = "application/json"
            # Daemon now only speaks JSON — CLI text mode and MCP render
            # locally from the same envelope (see
            # :mod:`agentcloak.core.text_renderers`). The header stays
            # explicit so a curl-style client doesn't accidentally negotiate
            # something unexpected if we ever add new media types.
            headers["Accept"] = "application/json"
            kwargs["headers"] = headers
            if params:
                kwargs["params"] = params
            resp = client.request(method, path, **kwargs)
            return self._parse_response(resp)

    async def _do_request_async(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self._base, timeout=self._request_timeout_s
        ) as client:
            kwargs: dict[str, Any] = {}
            headers: dict[str, str] = {}
            if json_body is not None:
                kwargs["content"] = orjson.dumps(json_body)
                headers["Content-Type"] = "application/json"
            headers["Accept"] = "application/json"
            kwargs["headers"] = headers
            if params:
                kwargs["params"] = params
            resp = await client.request(method, path, **kwargs)
            return self._parse_response(resp)

    def _parse_response(self, resp: httpx.Response) -> dict[str, Any]:
        """Decode a daemon response and raise on error envelope."""
        raw = resp.content
        try:
            data: dict[str, Any] = orjson.loads(raw) if raw else {}
        except orjson.JSONDecodeError as exc:
            raise AgentBrowserError(
                error="daemon_invalid_response",
                hint=(f"Daemon returned non-JSON body (HTTP {resp.status_code})"),
                action="check daemon logs for the unexpected response",
            ) from exc

        if not data.get("ok") and "error" in data:
            raise AgentBrowserError(
                error=str(data["error"]),
                hint=str(data.get("hint", "")),
                action=str(data.get("action", "")),
            )
        return data

    # ------------------------------------------------------------------
    # Auto-start: subprocess spawn + health polling
    # ------------------------------------------------------------------

    def _handle_connect_error_sync(
        self,
        exc: httpx.ConnectError,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None,
        params: dict[str, str] | None,
    ) -> dict[str, Any]:
        if not self._auto_start:
            raise DaemonConnectionError(
                error="daemon_unreachable",
                hint=f"Cannot connect to daemon at {self._host}:{self._port}",
                action=(
                    "run 'agentcloak daemon start -b' to launch, or "
                    "'agentcloak doctor --fix' if the install looks broken"
                ),
            ) from exc
        if self._auto_started:
            # We've already spawned a daemon in this client's lifetime — a
            # second failure is a hard failure, not a retry trigger.
            raise DaemonConnectionError(
                error="daemon_unreachable",
                hint=(
                    f"Daemon started but still unreachable at {self._host}:{self._port}"
                ),
                action=(
                    "check daemon logs (~/.agentcloak/logs/daemon.log) and "
                    "run 'agentcloak doctor --fix' to diagnose"
                ),
            ) from exc

        started = self._ensure_daemon_sync()
        if not started:
            raise DaemonConnectionError(
                error="daemon_auto_start_failed",
                hint=(
                    f"Cannot connect to daemon at {self._host}:{self._port} "
                    "and auto-start failed within the startup budget"
                ),
                action=(
                    "run 'agentcloak doctor --fix' (or 'uvx agentcloak "
                    "doctor --fix') to diagnose, then 'agentcloak daemon "
                    "start -b' to launch manually"
                ),
            ) from exc
        try:
            return self._do_request_sync(
                method, path, json_body=json_body, params=params
            )
        except httpx.ConnectError as retry_exc:
            raise DaemonConnectionError(
                error="daemon_unreachable",
                hint=(
                    f"Daemon started but still unreachable at {self._host}:{self._port}"
                ),
                action=(
                    "check daemon logs (~/.agentcloak/logs/daemon.log) and "
                    "run 'agentcloak doctor --fix' to diagnose"
                ),
            ) from retry_exc

    async def _handle_connect_error_async(
        self,
        exc: httpx.ConnectError,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None,
        params: dict[str, str] | None,
    ) -> dict[str, Any]:
        if not self._auto_start:
            raise DaemonConnectionError(
                error="daemon_unreachable",
                hint=f"Cannot connect to daemon at {self._host}:{self._port}",
                action=(
                    "run 'agentcloak daemon start -b' to launch, or "
                    "'agentcloak doctor --fix' if the install looks broken"
                ),
            ) from exc
        if self._auto_started:
            raise DaemonConnectionError(
                error="daemon_unreachable",
                hint=(
                    f"Daemon started but still unreachable at {self._host}:{self._port}"
                ),
                action=(
                    "check daemon logs (~/.agentcloak/logs/daemon.log) and "
                    "run 'agentcloak doctor --fix' to diagnose"
                ),
            ) from exc

        started = await self._ensure_daemon_async()
        if not started:
            raise DaemonConnectionError(
                error="daemon_auto_start_failed",
                hint=(
                    f"Cannot connect to daemon at {self._host}:{self._port} "
                    "and auto-start failed within the startup budget"
                ),
                action=(
                    "run 'agentcloak doctor --fix' (or 'uvx agentcloak "
                    "doctor --fix') to diagnose, then 'agentcloak daemon "
                    "start -b' to launch manually"
                ),
            ) from exc
        try:
            return await self._do_request_async(
                method, path, json_body=json_body, params=params
            )
        except httpx.ConnectError as retry_exc:
            raise DaemonConnectionError(
                error="daemon_unreachable",
                hint=(
                    f"Daemon started but still unreachable at {self._host}:{self._port}"
                ),
                action=(
                    "check daemon logs (~/.agentcloak/logs/daemon.log) and "
                    "run 'agentcloak doctor --fix' to diagnose"
                ),
            ) from retry_exc

    def _build_daemon_argv(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        headless: bool | None = None,
        profile: str | None = None,
        humanize: bool | None = None,
    ) -> list[str]:
        """Return the subprocess argv for spawning the daemon."""
        argv: list[str] = [sys.executable, "-m", "agentcloak.daemon"]
        if host:
            argv.extend(["--host", host])
        if port:
            argv.extend(["--port", str(port)])
        if headless is True:
            argv.append("--headless")
        elif headless is False:
            argv.append("--headed")
        if profile:
            argv.extend(["--profile", profile])
        if humanize is True:
            argv.append("--humanize")
        elif humanize is False:
            argv.append("--no-humanize")
        return argv

    def _spawn_daemon(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        headless: bool | None = None,
        profile: str | None = None,
        humanize: bool | None = None,
    ) -> subprocess.Popen[bytes]:
        """Launch the daemon as a background subprocess and return the handle."""
        argv = self._build_daemon_argv(
            host=host,
            port=port,
            headless=headless,
            profile=profile,
            humanize=humanize,
        )
        env = os.environ.copy()
        # Background daemons should log to a rotating file by default — the
        # user typically can't see stderr from a detached subprocess.
        env.setdefault("AGENTCLOAK_LOG_TO_FILE", "true")
        if sys.platform == "win32":
            return subprocess.Popen(
                argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                creationflags=subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        return subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )

    def spawn_background(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        headless: bool | None = None,
        profile: str | None = None,
        humanize: bool | None = None,
    ) -> int:
        """Public API: spawn daemon in background, return PID.

        Used by ``agentcloak daemon start -b`` and ``agentcloak profile launch -b``
        where the user explicitly asks for background mode. Auto-start uses the
        private ``_spawn_daemon`` path with health-check polling.
        """
        proc = self._spawn_daemon(
            host=host,
            port=port,
            headless=headless,
            profile=profile,
            humanize=humanize,
        )
        return proc.pid

    def _ensure_daemon_sync(
        self,
        *,
        headless: bool | None = None,
        profile: str | None = None,
        humanize: bool | None = None,
    ) -> bool:
        """Spawn the daemon and poll /health until it answers or we time out."""
        if self._auto_started:
            return False
        t0 = time.monotonic()
        # Auto-start is an expected, normal lifecycle event (the daemon
        # auto-starts on the first command) — emitted at ``info`` so it
        # doesn't bubble up under the default ``warning`` log level. Only
        # the failure path stays at ``warning`` since that one demands the
        # user's attention.
        logger.info("daemon_auto_starting", host=self._host, port=self._port)
        self._spawn_daemon(headless=headless, profile=profile, humanize=humanize)
        self._auto_started = True

        elapsed = 0.0
        while elapsed < self._startup_budget_s:
            time.sleep(self._poll_interval_s)
            elapsed += self._poll_interval_s
            try:
                with httpx.Client(
                    base_url=self._base, timeout=_HEALTH_PROBE_TIMEOUT_S
                ) as client:
                    resp = client.get("/health")
                    if resp.status_code == 200:
                        logger.info(
                            "daemon_auto_started",
                            elapsed_s=round(time.monotonic() - t0, 1),
                            outcome="success",
                        )
                        return True
            except httpx.ConnectError:
                continue
            except httpx.HTTPError:
                continue

        logger.warning(
            "daemon_auto_start_failed",
            elapsed_s=round(time.monotonic() - t0, 1),
            outcome="timeout",
        )
        return False

    async def _ensure_daemon_async(
        self,
        *,
        headless: bool | None = None,
        profile: str | None = None,
        humanize: bool | None = None,
    ) -> bool:
        """Async variant of :meth:`_ensure_daemon_sync`."""
        if self._auto_started:
            return False
        t0 = time.monotonic()
        # See the sync variant for the warning→info rationale.
        logger.info("daemon_auto_starting", host=self._host, port=self._port)
        self._spawn_daemon(headless=headless, profile=profile, humanize=humanize)
        self._auto_started = True

        elapsed = 0.0
        while elapsed < self._startup_budget_s:
            await asyncio.sleep(self._poll_interval_s)
            elapsed += self._poll_interval_s
            try:
                async with httpx.AsyncClient(
                    base_url=self._base, timeout=_HEALTH_PROBE_TIMEOUT_S
                ) as client:
                    resp = await client.get("/health")
                    if resp.status_code == 200:
                        logger.info(
                            "daemon_auto_started",
                            elapsed_s=round(time.monotonic() - t0, 1),
                            outcome="success",
                        )
                        return True
            except httpx.ConnectError:
                continue
            except httpx.HTTPError:
                continue

        logger.warning(
            "daemon_auto_start_failed",
            elapsed_s=round(time.monotonic() - t0, 1),
            outcome="timeout",
        )
        return False

    # ------------------------------------------------------------------
    # Public lifecycle helpers
    # ------------------------------------------------------------------

    async def launch_daemon(
        self,
        *,
        headless: bool = True,
        profile: str = "",
    ) -> dict[str, Any]:
        """Explicitly (re)start the daemon. Used by the MCP launch tool.

        If a daemon is already reachable we stop it first to honour the new
        flags, then auto-start a fresh instance. The response is the daemon's
        ``/health`` payload so the caller knows what tier ended up running.
        """
        try:
            async with httpx.AsyncClient(
                base_url=self._base, timeout=_HEALTH_PROBE_TIMEOUT_S
            ) as client:
                resp = await client.get("/health")
                if resp.status_code == 200:
                    await self._stop_daemon()
                    # Give the listener a moment to release the port.
                    await asyncio.sleep(1.0)
        except httpx.ConnectError:
            pass
        except httpx.HTTPError:
            pass

        self._auto_started = False
        ok = await self._ensure_daemon_async(
            headless=headless,
            profile=profile or None,
        )
        if not ok:
            raise DaemonConnectionError(
                error="daemon_launch_failed",
                hint="Daemon failed to start within the startup timeout",
                action="check logs or start manually with 'agentcloak daemon start -b'",
            )
        return await self._do_request_async("GET", "/health")

    async def _stop_daemon(self) -> None:
        try:
            async with httpx.AsyncClient(base_url=self._base, timeout=5.0) as client:
                await client.post("/shutdown")
        except Exception:
            # Best effort — shutdown failures are surfaced through the next
            # request, not from this helper.
            pass

    # ------------------------------------------------------------------
    # Typed sync API (CLI bespoke commands)
    # ------------------------------------------------------------------
    # The CLI dispatches most routes through ``_send_sync(method, path, body)``
    # directly and renders the JSON envelope locally. The wrappers below are
    # only kept for commands that reshape the response before rendering — base64
    # decode, custom file output, special error handling, and so on. See
    # ``scripts/generate_client.KEEP_SYNC_METHODS`` for the canonical list.

    def health_sync(self) -> dict[str, Any]:
        return self._send_sync("GET", "/health")

    def shutdown_sync(self) -> dict[str, Any]:
        try:
            return self._send_sync("POST", "/shutdown")
        except DaemonConnectionError:
            return {"ok": True}

    def screenshot_sync(
        self,
        *,
        full_page: bool = False,
        format: str = "jpeg",
        quality: int | None = None,
    ) -> dict[str, Any]:
        return self._send_sync(
            "GET",
            "/screenshot",
            params=_build_screenshot_params(
                full_page=full_page,
                format=format,
                quality=(
                    quality
                    if quality is not None
                    else self._cfg.browser.screenshot_quality
                ),
            ),
        )

    def fetch_sync(
        self,
        url: str,
        *,
        method: str = "GET",
        body: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return self._send_sync(
            "POST",
            "/fetch",
            json_body=_build_fetch_body(
                url=url,
                method=method,
                body=body,
                headers=headers,
                timeout=float(timeout)
                if timeout is not None
                else float(self._cfg.browser.navigation_timeout),
            ),
        )

    def capture_export_sync(self, *, fmt: str = "har") -> dict[str, Any]:
        return self._send_sync("GET", "/capture/export", params={"format": fmt})

    def capture_analyze_sync(self, *, domain: str = "") -> dict[str, Any]:
        params: dict[str, str] = {}
        if domain:
            params["domain"] = domain
        return self._send_sync("GET", "/capture/analyze", params=params)

    def profile_create_from_current_sync(self, *, name: str) -> dict[str, Any]:
        return self._send_sync(
            "POST", "/profile/create-from-current", json_body={"name": name}
        )

    def cookies_export_sync(self, *, url: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if url:
            body["url"] = url
        return self._send_sync("POST", "/cookies/export", json_body=body)

    def bridge_token_reset_sync(self) -> dict[str, Any]:
        """Hot-rotate the persistent bridge token via the running daemon."""
        return self._send_sync("POST", "/bridge/token/reset")

    def pdf_sync(
        self,
        *,
        format: str = "A4",
        landscape: bool = False,
        scale: float | None = None,
        margin: dict[str, Any] | None = None,
        page_ranges: str | None = None,
    ) -> dict[str, Any]:
        """Render the page to PDF and return base64 for the CLI to write.

        The CLI decodes the base64 and writes the file itself (it may target a
        remote daemon), mirroring ``screenshot_sync``. ``output_path`` is
        intentionally omitted here — the daemon returns bytes, the CLI owns the
        filesystem write.
        """
        return self._send_sync(
            "POST",
            "/pdf",
            json_body=_build_pdf_body(
                format=format,
                landscape=landscape,
                scale=scale,
                margin=margin,
                page_ranges=page_ranges,
                output_path=None,
            ),
        )

    # ------------------------------------------------------------------
    # Typed async API (MCP)
    # ------------------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        return await self._send_async("GET", "/health")

    async def shutdown(self) -> dict[str, Any]:
        try:
            return await self._send_async("POST", "/shutdown")
        except DaemonConnectionError:
            return {"ok": True}

    async def launch(
        self, *, tier: str = "auto", profile: str | None = None
    ) -> dict[str, Any]:
        """Hot-switch the daemon's active browser tier (async)."""
        body: dict[str, Any] = {"tier": tier}
        if profile is not None:
            body["profile"] = profile
        return await self._send_async("POST", "/launch", json_body=body)

    async def navigate(
        self,
        url: str,
        *,
        timeout: float | None = None,
        include_snapshot: bool = False,
        snapshot_mode: str = "compact",
    ) -> dict[str, Any]:
        body = _build_navigate_body(
            url=url,
            timeout=(
                float(timeout)
                if timeout is not None
                else float(self._cfg.browser.navigation_timeout)
            ),
            include_snapshot=include_snapshot,
            snapshot_mode=snapshot_mode,
        )
        return await self._send_async("POST", "/navigate", json_body=body)

    async def screenshot(
        self,
        *,
        full_page: bool = False,
        format: str = "jpeg",
        quality: int | None = None,
    ) -> dict[str, Any]:
        # MCP defaults to ``mcp_screenshot_quality`` (lower than CLI's 80) so
        # base64 output stays under typical MCP token budgets.
        return await self._send_async(
            "GET",
            "/screenshot",
            params=_build_screenshot_params(
                full_page=full_page,
                format=format,
                quality=quality
                if quality is not None
                else self._cfg.browser.mcp_screenshot_quality,
            ),
        )

    async def snapshot(
        self,
        *,
        mode: str = "compact",
        max_chars: int = 0,
        max_nodes: int = -1,
        focus: int = 0,
        offset: int = 0,
        frames: bool = False,
        diff: bool = False,
        include_selector_map: bool = False,
    ) -> dict[str, Any]:
        return await self._send_async(
            "GET",
            "/snapshot",
            params=_build_snapshot_params(
                mode=mode,
                max_chars=max_chars,
                max_nodes=max_nodes,
                focus=focus,
                offset=offset,
                frames=frames,
                diff=diff,
                include_selector_map=include_selector_map,
            ),
        )

    async def evaluate(
        self,
        js: str,
        *,
        world: str = "main",
        max_return_size: int | None = None,
    ) -> dict[str, Any]:
        return await self._send_async(
            "POST",
            "/evaluate",
            json_body={
                "js": js,
                "world": world,
                "max_return_size": max_return_size
                if max_return_size is not None
                else self._cfg.browser.max_return_size,
            },
        )

    async def network(self, *, since: str | int = 0) -> dict[str, Any]:
        return await self._send_async("GET", "/network", params={"since": str(since)})

    async def action(
        self,
        kind: str,
        *,
        index: int | None = None,
        target: str | None = None,
        include_snapshot: bool = False,
        snapshot_mode: str = "compact",
        **kwargs: Any,
    ) -> dict[str, Any]:
        body = _build_action_body(
            kind=kind,
            index=index,
            target=target,
            include_snapshot=include_snapshot,
            snapshot_mode=snapshot_mode,
            extras=kwargs,
        )
        return await self._send_async("POST", "/action", json_body=body)

    async def action_batch(
        self,
        actions: list[dict[str, Any]],
        *,
        sleep: float = 0.0,
    ) -> dict[str, Any]:
        return await self._send_async(
            "POST",
            "/action/batch",
            json_body={"actions": actions, "sleep": sleep},
        )

    async def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        body: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return await self._send_async(
            "POST",
            "/fetch",
            json_body=_build_fetch_body(
                url=url,
                method=method,
                body=body,
                headers=headers,
                timeout=float(timeout)
                if timeout is not None
                else float(self._cfg.browser.navigation_timeout),
            ),
        )

    # --- Capture (async) ---

    async def capture_start(self) -> dict[str, Any]:
        return await self._send_async("POST", "/capture/start")

    async def capture_stop(self) -> dict[str, Any]:
        return await self._send_async("POST", "/capture/stop")

    async def capture_status(self) -> dict[str, Any]:
        return await self._send_async("GET", "/capture/status")

    async def capture_export(self, *, fmt: str = "har") -> dict[str, Any]:
        return await self._send_async("GET", "/capture/export", params={"format": fmt})

    async def capture_analyze(self, *, domain: str = "") -> dict[str, Any]:
        params: dict[str, str] = {}
        if domain:
            params["domain"] = domain
        return await self._send_async("GET", "/capture/analyze", params=params)

    async def capture_clear(self) -> dict[str, Any]:
        return await self._send_async("POST", "/capture/clear")

    async def capture_replay(self, *, url: str, method: str = "GET") -> dict[str, Any]:
        return await self._send_async(
            "POST",
            "/capture/replay",
            json_body={"url": url, "method": method},
        )

    # --- Profile (async) ---

    async def profile_list(self) -> dict[str, Any]:
        return await self._send_async("GET", "/profile/list")

    async def profile_create(self, *, name: str) -> dict[str, Any]:
        return await self._send_async(
            "POST", "/profile/create", json_body={"name": name}
        )

    async def profile_delete(self, *, name: str) -> dict[str, Any]:
        return await self._send_async(
            "POST", "/profile/delete", json_body={"name": name}
        )

    async def profile_create_from_current(self, *, name: str) -> dict[str, Any]:
        return await self._send_async(
            "POST", "/profile/create-from-current", json_body={"name": name}
        )

    # --- CDP / Tabs (async) ---

    async def cdp_endpoint(self) -> dict[str, Any]:
        return await self._send_async("GET", "/cdp/endpoint")

    async def tab_list(self) -> dict[str, Any]:
        return await self._send_async("GET", "/tabs")

    async def tab_new(self, *, url: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if url:
            body["url"] = url
        return await self._send_async("POST", "/tab/new", json_body=body)

    async def tab_close(self, tab_id: int) -> dict[str, Any]:
        return await self._send_async(
            "POST", "/tab/close", json_body={"tab_id": tab_id}
        )

    async def tab_switch(self, tab_id: int) -> dict[str, Any]:
        return await self._send_async(
            "POST", "/tab/switch", json_body={"tab_id": tab_id}
        )

    # --- Resume / Cookies / Bridge / Dialog / Wait / Upload / Frame (async) ---

    async def resume(self) -> dict[str, Any]:
        return await self._send_async("GET", "/resume")

    async def cookies_export(self, *, url: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if url:
            body["url"] = url
        return await self._send_async("POST", "/cookies/export", json_body=body)

    async def cookies_import(self, *, cookies: list[dict[str, Any]]) -> dict[str, Any]:
        return await self._send_async(
            "POST", "/cookies/import", json_body={"cookies": cookies}
        )

    async def dialog_status(self) -> dict[str, Any]:
        return await self._send_async("GET", "/dialog/status")

    async def dialog_handle(
        self, action_type: str, *, text: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"action": action_type}
        if text is not None:
            body["text"] = text
        return await self._send_async("POST", "/dialog/handle", json_body=body)

    async def wait(
        self,
        *,
        condition: str,
        value: str = "",
        timeout: int | None = None,
        state: str = "visible",
    ) -> dict[str, Any]:
        return await self._send_async(
            "POST",
            "/wait",
            json_body={
                "condition": condition,
                "value": value,
                "timeout": timeout
                if timeout is not None
                else self._cfg.browser.action_timeout,
                "state": state,
            },
        )

    async def upload(self, *, index: int, files: list[str]) -> dict[str, Any]:
        return await self._send_async(
            "POST", "/upload", json_body={"index": index, "files": files}
        )

    async def frame_list(self) -> dict[str, Any]:
        return await self._send_async("GET", "/frame/list")

    async def frame_focus(
        self,
        *,
        name: str | None = None,
        url: str | None = None,
        main: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"main": main}
        if name is not None:
            body["name"] = name
        if url is not None:
            body["url"] = url
        return await self._send_async("POST", "/frame/focus", json_body=body)

    async def bridge_claim(
        self,
        *,
        tab_id: int | None = None,
        url_pattern: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if tab_id is not None:
            body["tab_id"] = tab_id
        if url_pattern is not None:
            body["url_pattern"] = url_pattern
        return await self._send_async("POST", "/bridge/claim", json_body=body)

    async def bridge_finalize(self, *, mode: str = "close") -> dict[str, Any]:
        return await self._send_async(
            "POST", "/bridge/finalize", json_body={"mode": mode}
        )

    async def bridge_token_reset(self) -> dict[str, Any]:
        """Hot-rotate the persistent bridge token via the running daemon."""
        return await self._send_async("POST", "/bridge/token/reset")

    # --- Spell (async) ---

    async def spell_list(self) -> dict[str, Any]:
        return await self._send_async("GET", "/spell/list")

    async def spell_run(
        self, *, name: str, args: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return await self._send_async(
            "POST",
            "/spell/run",
            json_body={"name": name, "args": args or {}},
        )

    # --- Console (async, 7a R1) ---

    async def console(
        self, *, since: int = 0, limit: int = 0, level: str = ""
    ) -> dict[str, Any]:
        params: dict[str, str] = {"since": str(since)}
        if limit:
            params["limit"] = str(limit)
        if level:
            params["level"] = level
        return await self._send_async("GET", "/console", params=params)

    async def console_clear(self) -> dict[str, Any]:
        return await self._send_async("POST", "/console/clear")

    # --- Download (async, 7a R2) ---

    async def download_url(
        self, *, url: str, output_dir: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"url": url}
        if output_dir:
            body["output_dir"] = output_dir
        return await self._send_async("POST", "/download/url", json_body=body)

    async def download_wait(
        self, *, output_dir: str | None = None, timeout: float | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if output_dir:
            body["output_dir"] = output_dir
        if timeout is not None:
            body["timeout"] = timeout
        return await self._send_async("POST", "/download/wait", json_body=body)

    async def download_list(self) -> dict[str, Any]:
        return await self._send_async("GET", "/download/list")

    # --- Storage (async, 7a R4) ---

    async def storage_get(
        self, *, type: str = "local", key: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"type": type}
        if key is not None:
            body["key"] = key
        return await self._send_async("POST", "/storage/get", json_body=body)

    async def storage_set(
        self, *, type: str = "local", key: str, value: str
    ) -> dict[str, Any]:
        return await self._send_async(
            "POST", "/storage/set", json_body={"type": type, "key": key, "value": value}
        )

    async def storage_delete(self, *, type: str = "local", key: str) -> dict[str, Any]:
        return await self._send_async(
            "POST", "/storage/delete", json_body={"type": type, "key": key}
        )

    async def storage_clear(self, *, type: str = "local") -> dict[str, Any]:
        return await self._send_async(
            "POST", "/storage/clear", json_body={"type": type}
        )

    # --- Clipboard (async, 7a R5) ---

    async def clipboard_read(self) -> dict[str, Any]:
        return await self._send_async("GET", "/clipboard/read")

    async def clipboard_write(self, *, text: str) -> dict[str, Any]:
        return await self._send_async(
            "POST", "/clipboard/write", json_body={"text": text}
        )

    # --- PDF (async, 7a R6) ---

    async def pdf(
        self,
        *,
        format: str = "A4",
        landscape: bool = False,
        scale: float | None = None,
        margin: dict[str, Any] | None = None,
        page_ranges: str | None = None,
        output_path: str | None = None,
    ) -> dict[str, Any]:
        return await self._send_async(
            "POST",
            "/pdf",
            json_body=_build_pdf_body(
                format=format,
                landscape=landscape,
                scale=scale,
                margin=margin,
                page_ranges=page_ranges,
                output_path=output_path,
            ),
        )

    # --- Serve (async, 7a R7) ---

    async def serve_start(
        self, *, directory: str, port: int | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"directory": directory}
        if port is not None:
            body["port"] = port
        return await self._send_async("POST", "/serve/start", json_body=body)

    async def serve_stop(self) -> dict[str, Any]:
        return await self._send_async("POST", "/serve/stop")

    async def serve_status(self) -> dict[str, Any]:
        return await self._send_async("GET", "/serve/status")

    # --- Cookies CRUD (async, 7a R3) ---

    async def cookies_set(
        self,
        *,
        cookies: list[dict[str, Any]] | None = None,
        curl: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if cookies is not None:
            body["cookies"] = cookies
        if curl is not None:
            body["curl"] = curl
        return await self._send_async("POST", "/cookies/set", json_body=body)

    async def cookies_clear(self) -> dict[str, Any]:
        return await self._send_async("POST", "/cookies/clear")

    async def cookies_delete(
        self, *, name: str, domain: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name}
        if domain is not None:
            body["domain"] = domain
        return await self._send_async("POST", "/cookies/delete", json_body=body)

    # --- Init scripts (async, 7b T1.1) ---

    async def script_add(self, *, js: str = "", preset: str = "") -> dict[str, Any]:
        body: dict[str, Any] = {}
        if js:
            body["js"] = js
        if preset:
            body["preset"] = preset
        return await self._send_async("POST", "/script/add", json_body=body)

    async def script_remove(self, *, identifier: str) -> dict[str, Any]:
        return await self._send_async(
            "POST", "/script/remove", json_body={"identifier": identifier}
        )

    async def script_list(self) -> dict[str, Any]:
        return await self._send_async("GET", "/script/list")

    # --- Network route interception (async, 7b T1.3) ---

    async def route_add(
        self,
        *,
        pattern: str,
        action: str = "continue",
        resource_type: str = "",
        method: str = "",
        status: int = 0,
        content_type: str = "",
        body: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"pattern": pattern, "action": action}
        if resource_type:
            payload["resource_type"] = resource_type
        if method:
            payload["method"] = method
        if status:
            payload["status"] = status
        if content_type:
            payload["content_type"] = content_type
        if body:
            payload["body"] = body
        return await self._send_async("POST", "/route/add", json_body=payload)

    async def route_remove(self, *, pattern: str = "") -> dict[str, Any]:
        body: dict[str, Any] = {}
        if pattern:
            body["pattern"] = pattern
        return await self._send_async("POST", "/route/remove", json_body=body)

    async def route_list(self) -> dict[str, Any]:
        return await self._send_async("GET", "/route/list")

    # --- Streaming capture: WebSocket + SSE (async, 7b T2) ---

    async def ws_list(self) -> dict[str, Any]:
        return await self._send_async("GET", "/ws/list")

    async def ws_messages(self, *, since: int = 0) -> dict[str, Any]:
        return await self._send_async(
            "GET", "/ws/messages", params={"since": str(since)}
        )

    async def sse_messages(self, *, since: int = 0) -> dict[str, Any]:
        return await self._send_async(
            "GET", "/sse/messages", params={"since": str(since)}
        )

    # --- Header injection (async, 7b T1.2) ---

    async def emulation_headers(self, *, headers: dict[str, str]) -> dict[str, Any]:
        return await self._send_async(
            "POST", "/emulation/headers", json_body={"headers": headers}
        )

    # --- GraphQL (async, 7b T1.4) ---

    async def graphql_introspect(
        self, *, url: str, headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"url": url}
        if headers:
            payload["headers"] = headers
        return await self._send_async("POST", "/graphql/introspect", json_body=payload)

    async def graphql_query(
        self,
        *,
        url: str,
        query: str,
        variables: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"url": url, "query": query}
        if variables:
            payload["variables"] = variables
        if headers:
            payload["headers"] = headers
        return await self._send_async("POST", "/graphql/query", json_body=payload)

    # --- Debugger (async, 7b T3) ---

    async def debugger_enable(self) -> dict[str, Any]:
        return await self._send_async("POST", "/debugger/enable")

    async def debugger_disable(self) -> dict[str, Any]:
        return await self._send_async("POST", "/debugger/disable")

    async def debugger_breakpoint_set(
        self, *, url: str, line: int, condition: str = ""
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"url": url, "line": line}
        if condition:
            body["condition"] = condition
        return await self._send_async(
            "POST", "/debugger/breakpoint/set", json_body=body
        )

    async def debugger_breakpoint_remove(self, *, breakpoint_id: str) -> dict[str, Any]:
        return await self._send_async(
            "POST",
            "/debugger/breakpoint/remove",
            json_body={"breakpoint_id": breakpoint_id},
        )

    async def debugger_breakpoint_list(self) -> dict[str, Any]:
        return await self._send_async("GET", "/debugger/breakpoint/list")

    async def debugger_xhr_breakpoint_set(
        self, *, url_pattern: str = ""
    ) -> dict[str, Any]:
        return await self._send_async(
            "POST",
            "/debugger/xhr-breakpoint/set",
            json_body={"url_pattern": url_pattern},
        )

    async def debugger_xhr_breakpoint_remove(
        self, *, url_pattern: str = ""
    ) -> dict[str, Any]:
        return await self._send_async(
            "POST",
            "/debugger/xhr-breakpoint/remove",
            json_body={"url_pattern": url_pattern},
        )

    async def debugger_resume(self) -> dict[str, Any]:
        return await self._send_async("POST", "/debugger/resume")

    async def debugger_step(self, *, type: str = "over") -> dict[str, Any]:
        return await self._send_async(
            "POST", "/debugger/step", json_body={"type": type}
        )

    async def debugger_paused_info(self) -> dict[str, Any]:
        return await self._send_async("GET", "/debugger/paused-info")

    async def debugger_scope_variables(self, *, object_id: str) -> dict[str, Any]:
        return await self._send_async(
            "POST", "/debugger/scope-variables", json_body={"object_id": object_id}
        )

    async def debugger_evaluate(
        self, *, call_frame_id: str, expression: str
    ) -> dict[str, Any]:
        return await self._send_async(
            "POST",
            "/debugger/evaluate",
            json_body={"call_frame_id": call_frame_id, "expression": expression},
        )

    async def debugger_scripts(self) -> dict[str, Any]:
        return await self._send_async("GET", "/debugger/scripts")

    async def debugger_script_source(self, *, script_id: str) -> dict[str, Any]:
        return await self._send_async(
            "POST", "/debugger/script-source", json_body={"script_id": script_id}
        )

    async def debugger_search(
        self,
        *,
        script_id: str,
        query: str,
        is_regex: bool = False,
        case_sensitive: bool = False,
    ) -> dict[str, Any]:
        return await self._send_async(
            "POST",
            "/debugger/search",
            json_body={
                "script_id": script_id,
                "query": query,
                "is_regex": is_regex,
                "case_sensitive": case_sensitive,
            },
        )

    async def debugger_skip_pauses(self, *, skip: bool) -> dict[str, Any]:
        return await self._send_async(
            "POST", "/debugger/skip-pauses", json_body={"skip": skip}
        )

    # --- SourceMap (async, 7b T4) ---

    async def sourcemap_list(self) -> dict[str, Any]:
        return await self._send_async("GET", "/sourcemap/list")

    async def sourcemap_get(self, *, script_id: str) -> dict[str, Any]:
        return await self._send_async(
            "POST", "/sourcemap/get", json_body={"script_id": script_id}
        )

    async def sourcemap_lookup(
        self, *, script_id: str, line: int, column: int = 0
    ) -> dict[str, Any]:
        return await self._send_async(
            "POST",
            "/sourcemap/lookup",
            json_body={"script_id": script_id, "line": line, "column": column},
        )

    async def sourcemap_sources(self, *, script_id: str) -> dict[str, Any]:
        return await self._send_async(
            "POST", "/sourcemap/sources", json_body={"script_id": script_id}
        )

    async def sourcemap_source_content(
        self, *, script_id: str, source_path: str
    ) -> dict[str, Any]:
        return await self._send_async(
            "POST",
            "/sourcemap/source-content",
            json_body={"script_id": script_id, "source_path": source_path},
        )


# ----------------------------------------------------------------------
# Body / param builders — defined once, used by both sync and async API.
# ----------------------------------------------------------------------


def _build_navigate_body(
    *,
    url: str,
    timeout: float,
    include_snapshot: bool,
    snapshot_mode: str,
) -> dict[str, Any]:
    body: dict[str, Any] = {"url": url, "timeout": timeout}
    if include_snapshot:
        body["include_snapshot"] = True
        body["snapshot_mode"] = snapshot_mode
    return body


def _build_screenshot_params(
    *,
    full_page: bool,
    format: str,
    quality: int,
) -> dict[str, str]:
    params: dict[str, str] = {"format": format, "quality": str(quality)}
    if full_page:
        params["full_page"] = "true"
    return params


def _build_snapshot_params(
    *,
    mode: str,
    max_chars: int,
    max_nodes: int,
    focus: int,
    offset: int,
    frames: bool,
    diff: bool,
    include_selector_map: bool,
) -> dict[str, str]:
    params: dict[str, str] = {"mode": mode}
    if not include_selector_map:
        params["include_selector_map"] = "false"
    if max_chars:
        params["max_chars"] = str(max_chars)
    # ``max_nodes`` uses a tri-state sentinel: ``-1`` means "caller didn't
    # specify, let the daemon apply ``config.browser.snapshot_max_nodes`` in compact
    # mode"; ``0`` means "explicit no limit"; ``>0`` is the explicit cap. We
    # forward ``0`` so the daemon can tell it apart from the default — a plain
    # truthiness check would swallow ``0`` and let the daemon auto-cap it.
    if max_nodes != -1:
        params["max_nodes"] = str(max_nodes)
    if focus:
        params["focus"] = str(focus)
    if offset:
        params["offset"] = str(offset)
    if frames:
        params["frames"] = "true"
    if diff:
        params["diff"] = "true"
    return params


def _build_action_body(
    *,
    kind: str,
    index: int | None,
    target: str | None,
    include_snapshot: bool,
    snapshot_mode: str,
    extras: dict[str, Any],
) -> dict[str, Any]:
    body: dict[str, Any] = {"kind": kind}
    if index is not None:
        body["index"] = index
    if target is not None:
        body["target"] = target
    if include_snapshot:
        body["include_snapshot"] = True
        body["snapshot_mode"] = snapshot_mode
    body.update(extras)
    return body


def _build_fetch_body(
    *,
    url: str,
    method: str,
    body: str | None,
    headers: dict[str, str] | None,
    timeout: float,
) -> dict[str, Any]:
    json_body: dict[str, Any] = {"url": url, "method": method, "timeout": timeout}
    if body is not None:
        json_body["body"] = body
    if headers is not None:
        json_body["headers"] = headers
    return json_body


def _build_pdf_body(
    *,
    format: str,
    landscape: bool,
    scale: float | None,
    margin: dict[str, Any] | None,
    page_ranges: str | None,
    output_path: str | None,
) -> dict[str, Any]:
    """Assemble the ``/pdf`` request body, omitting unset optional fields."""
    body: dict[str, Any] = {"format": format, "landscape": landscape}
    if scale is not None:
        body["scale"] = scale
    if margin is not None:
        body["margin"] = margin
    if page_ranges:
        body["page_ranges"] = page_ranges
    if output_path:
        body["output_path"] = output_path
    return body
