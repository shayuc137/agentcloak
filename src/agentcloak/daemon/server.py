"""Daemon lifecycle — start, stop, health check.

Responsibilities are split between three entry points:

- ``create_app()`` (in ``app.py``) builds the FastAPI app.
- ``start()`` launches the browser, wires ``app.state``, then drives uvicorn.
- ``stop()`` / ``health()`` use synchronous httpx calls for cross-process
  control.

uvicorn runs in-process: we instantiate a ``Server`` and drive its
lifecycle from coroutines so we can interleave the browser shutdown
sequence with the HTTP server's graceful close.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
import time
from typing import TYPE_CHECKING, Any

import httpx
import orjson
import structlog
import uvicorn

from agentcloak.browser import create_context
from agentcloak.browser.cloak_ctx import TURNSTILE_PATCH_DIR
from agentcloak.browser.xvfb import XvfbManager
from agentcloak.core.config import (
    Paths,
    ensure_bridge_token,
    load_config,
    write_example_config,
)
from agentcloak.core.types import StealthTier
from agentcloak.daemon.app import configure_app_state, create_app
from agentcloak.daemon.context_manager import ContextManager

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["health", "start", "stop"]

logger = structlog.get_logger()

_PORT_RANGE_SIZE = 10

# Anchor for the log file handle when ``log_to_file`` is enabled. Kept at
# module scope so it survives until process exit; see ``log_target`` setup in
# :func:`start` for the full context.
_file_log_handle: Any = None


def _rotate_log_if_needed(log_path: Path, max_bytes: int, backup_count: int) -> None:
    """One-shot rotation at daemon startup.

    The daemon runs long enough that the log can grow significantly between
    restarts, so we trim once on launch. We deliberately do *not* use
    :class:`logging.handlers.RotatingFileHandler` because it registers with
    stdlib logging and gets closed by ``logging.shutdown()`` when uvicorn
    reconfigures logging (see ``log_target`` block in :func:`start`).
    """
    if not log_path.exists():
        return
    try:
        size = log_path.stat().st_size
    except OSError:
        return
    if size < max_bytes:
        return

    # Shift backups: daemon.log.{N-1} -> daemon.log.N, drop the oldest.
    for i in range(backup_count, 0, -1):
        src = log_path.with_suffix(f".log.{i}") if i > 1 else log_path
        dst = log_path.with_suffix(f".log.{i}")
        if src.exists() and i < backup_count:
            with contextlib.suppress(OSError):
                src.replace(dst)
        elif i == backup_count and src.exists():
            with contextlib.suppress(OSError):
                src.unlink()


def _pid_file(paths: Paths) -> Path:
    return paths.root / "daemon.pid"


def _write_pid(paths: Paths) -> None:
    paths.ensure_dirs()
    _pid_file(paths).write_text(str(os.getpid()))


def _clear_pid(paths: Paths) -> None:
    pf = _pid_file(paths)
    if pf.exists():
        pf.unlink()


def _write_session(
    paths: Paths,
    *,
    port: int,
    tier: StealthTier,
    profile: str | None = None,
    bridge_token: str | None = None,
) -> None:
    data: dict[str, object] = {
        "pid": os.getpid(),
        "port": port,
        "stealth_tier": tier.value,
        "profile": profile,
        "bridge_token": bridge_token,
    }
    paths.ensure_dirs()
    paths.active_session_file.write_bytes(orjson.dumps(data))
    with contextlib.suppress(OSError):
        os.chmod(str(paths.active_session_file), 0o600)


def _clear_session(paths: Paths) -> None:
    if paths.active_session_file.exists():
        paths.active_session_file.unlink()


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _check_stale_pid(paths: Paths) -> bool:
    pf = _pid_file(paths)
    if not pf.exists():
        return False
    try:
        pid = int(pf.read_text().strip())
    except (ValueError, OSError):
        _clear_pid(paths)
        _clear_session(paths)
        return False
    if not _pid_alive(pid):
        _clear_pid(paths)
        _clear_session(paths)
        return False

    # Process exists — verify it's actually an agentcloak daemon via /health.
    import json
    import urllib.request

    _, _stale_cfg = load_config()
    try:
        session_data = json.loads(paths.active_session_file.read_text())
        host = session_data.get("host", _stale_cfg.daemon.host)
        port = session_data.get("port", _stale_cfg.daemon.port)
        url = f"http://{host}:{port}/health"
        with urllib.request.urlopen(url, timeout=1) as resp:
            data = json.loads(resp.read())
            if data.get("ok"):
                return True  # genuinely running
    except Exception:
        pass
    _clear_pid(paths)
    _clear_session(paths)
    return False


def _diagnose_launch_failure(
    exc: BaseException, *, tier: StealthTier, headless: bool
) -> None:
    """Emit a structured warning that maps Playwright errors to fix hints.

    We can't change the exception type (the caller re-raises), but we can
    log a friendly diagnosis to the daemon log so when the user runs
    ``tail ~/.agentcloak/logs/daemon.log`` they get an answer instead of a
    raw Python traceback. The CLI ``daemon start -b`` path benefits the
    most — it backgrounds the daemon and the user never sees stderr.
    """
    message = str(exc).lower()

    if "xvfb" in message or "cannot open display" in message or "display :" in message:
        logger.error(
            "browser_launch_failed",
            cause="missing_display",
            tier=tier.value,
            hint=(
                "no X/Wayland display and Xvfb is not running; install Xvfb "
                "(see 'agentcloak doctor --fix') or set headless=true"
            ),
        )
        return

    if (
        "libnss" in message
        or "libgbm" in message
        or "shared object" in message
        or "error while loading" in message
    ):
        logger.error(
            "browser_launch_failed",
            cause="missing_system_libs",
            tier=tier.value,
            hint=(
                "Chromium's runtime libs are missing; run "
                "'sudo playwright install-deps chromium' "
                "(or 'agentcloak doctor --fix --sudo')"
            ),
        )
        return

    if "no such file" in message and ("chrome" in message or "chromium" in message):
        logger.error(
            "browser_launch_failed",
            cause="missing_browser_binary",
            tier=tier.value,
            hint=(
                "CloakBrowser binary not downloaded; run "
                "'agentcloak doctor --fix' to fetch the ~200MB bundle"
            ),
        )
        return

    if "permission denied" in message:
        logger.error(
            "browser_launch_failed",
            cause="permission_denied",
            tier=tier.value,
            hint=(
                "filesystem permission error launching the browser binary; "
                "check ~/.cloakbrowser/ ownership and SELinux/AppArmor policy"
            ),
        )
        return

    logger.error(
        "browser_launch_failed",
        cause="unknown",
        tier=tier.value,
        headless=headless,
        error=str(exc),
        hint=(
            "run 'agentcloak doctor --fix' to verify dependencies; "
            "check ~/.agentcloak/logs/daemon.log for the traceback"
        ),
    )


async def _try_bind_port(
    *,
    config: uvicorn.Config,
    host: str,
    base_port: int,
) -> tuple[uvicorn.Server, int]:
    """Try base_port, base_port+1, ... until one binds. Return (server, port).

    uvicorn handles binding internally — we probe by attempting a transient
    asyncio listener first so failures don't leave a half-initialized server
    behind.
    """
    last_error: OSError | None = None
    for offset in range(_PORT_RANGE_SIZE):
        try_port = base_port + offset
        # Probe the port first with a transient asyncio server.
        loop = asyncio.get_running_loop()
        try:
            sock_server = await loop.create_server(
                lambda: asyncio.Protocol(), host, try_port
            )
        except OSError as exc:
            last_error = exc
            if offset < _PORT_RANGE_SIZE - 1:
                logger.info(
                    "port_in_use",
                    port=try_port,
                    next_port=try_port + 1,
                )
            continue
        sock_server.close()
        await sock_server.wait_closed()

        config.port = try_port
        server = uvicorn.Server(config=config)
        return server, try_port

    assert last_error is not None
    raise last_error


async def start(
    *,
    host: str | None = None,
    port: int | None = None,
    headless: bool | None = None,
    profile: str | None = None,
    humanize: bool | None = None,
) -> None:
    """Start the daemon server (blocking)."""
    paths, cfg = load_config()

    actual_host = host or cfg.daemon.host
    actual_port = port or cfg.daemon.port

    if _check_stale_pid(paths):
        logger.error("daemon_already_running", pid_file=str(_pid_file(paths)))
        sys.exit(1)

    from agentcloak.core.config import resolve_tier

    resolved = resolve_tier(cfg.browser.default_tier)
    tier = StealthTier(resolved)
    actual_headless = headless if headless is not None else cfg.browser.headless
    actual_humanize = humanize if humanize is not None else cfg.browser.humanize
    xvfb_mgr: XvfbManager | None = None

    # remote_bridge tier starts with no local browser at all — the
    # context_manager waits for an extension to connect before any
    # browser-dependent route can succeed.
    skip_local_launch = tier == StealthTier.REMOTE_BRIDGE

    local_proxy: Any = None
    proxy_url: str | None = None

    if tier == StealthTier.CLOAK and not skip_local_launch:
        needs_xvfb = (
            not actual_headless
            and sys.platform != "win32"
            and not os.environ.get("DISPLAY")
        )
        if needs_xvfb:
            xvfb_mgr = XvfbManager(
                width=cfg.browser.viewport_width, height=cfg.browser.viewport_height
            )
            await xvfb_mgr.ensure_display()

        try:
            from httpcloak import LocalProxy  # pyright: ignore[reportMissingImports,reportMissingTypeStubs,reportUnknownVariableType]  # noqa: I001

            import cloakbrowser  # pyright: ignore[reportMissingTypeStubs]

            chrome_major = cloakbrowser.CHROMIUM_VERSION.split(".")[0]
            preset = f"chrome-{chrome_major}"
            try:
                local_proxy = LocalProxy(  # pyright: ignore[reportUnknownVariableType]
                    port=0, preset=preset, tls_only=True
                )
            except (ValueError, KeyError):
                logger.warning("httpcloak_preset_fallback", wanted=preset)
                local_proxy = LocalProxy(  # pyright: ignore[reportUnknownVariableType]
                    port=0, preset="chrome-latest", tls_only=True
                )
            proxy_url = str(local_proxy.proxy_url)  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
            logger.info("local_proxy_started", url=proxy_url, preset=preset)
        except ImportError:
            logger.warning(
                "httpcloak_not_installed",
                hint="fetch requests will use plain httpx (TLS fingerprint exposed)",
            )
        except Exception as exc:
            logger.warning("local_proxy_failed", error=str(exc))

    # Persistent bridge token — survives daemon restarts so the extension
    # only needs to be paired once. ``ensure_bridge_token`` mutates cfg in
    # place when it has to generate, so subsequent ``load_config`` calls
    # see the same value.
    bridge_token = ensure_bridge_token(paths, cfg)

    # Refresh the annotated example config every start. Always overwritten
    # (the file is documentation, not user state); the user's actual
    # ``config.toml`` is left strictly alone.
    with contextlib.suppress(OSError):
        write_example_config(paths)

    _write_pid(paths)

    log_target = sys.stderr
    # Open the log file directly instead of going through ``RotatingFileHandler``.
    # The handler gets *registered* with the stdlib ``logging`` module on
    # construction, and ``uvicorn.Config(...)`` later calls
    # ``logging.config.dictConfig`` which invokes ``logging.shutdown()`` on
    # every existing handler — that closed our stream and made the very next
    # ``logger.info`` raise ``I/O operation on closed file``. We do a single
    # size-based rotation at startup; the daemon is long-lived but the log
    # stays bounded across restarts.
    global _file_log_handle
    if cfg.daemon.log_to_file:
        log_path = paths.logs_dir / "daemon.log"
        _rotate_log_if_needed(
            log_path, cfg.daemon.log_max_bytes, cfg.daemon.log_backup_count
        )
        _file_log_handle = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
        log_target = _file_log_handle  # type: ignore[assignment]

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=log_target),
    )

    idle_timeout = cfg.browser.idle_timeout_min * 60

    profile_dir: Path | None = None
    if profile:
        profile_dir = paths.profiles_dir / profile
        profile_dir.mkdir(parents=True, exist_ok=True)

    # Assemble the Chromium command-line flags from user config. The
    # ``--disable-features=DnsOverHttps`` default keeps split-horizon DNS
    # working with transparent proxies — users who want Chromium's
    # bundled DoH set ``dns_over_https = true``.
    chromium_args: list[str] = list(cfg.browser.extra_args)
    if not cfg.browser.dns_over_https:
        chromium_args.append("--disable-features=DnsOverHttps")

    raw_ctx: Any = None
    if not skip_local_launch:
        logger.info(
            "launching_browser",
            tier=tier.value,
            headless=actual_headless,
            humanize=actual_humanize,
            profile=profile,
            browser_proxy=cfg.browser.proxy or None,
            extra_args=chromium_args,
        )
        try:
            raw_ctx = await create_context(
                tier=tier,
                headless=actual_headless,
                viewport_width=cfg.browser.viewport_width,
                viewport_height=cfg.browser.viewport_height,
                profile_dir=profile_dir,
                humanize=actual_humanize,
                extensions=[str(TURNSTILE_PATCH_DIR)]
                if tier == StealthTier.CLOAK
                else None,
                proxy_url=proxy_url,
                browser_proxy=cfg.browser.proxy or None,
                extra_args=chromium_args,
                browser_config=cfg.browser,
            )
        except Exception as exc:
            # Browser launch failures are the most common first-run problem.
            # Surface the underlying cause with an actionable hint instead of a
            # raw Python exception. We pattern-match on the error text rather
            # than the exception class because Playwright wraps everything in
            # generic ``Error`` subclasses without stable error codes.
            _diagnose_launch_failure(exc, tier=tier, headless=actual_headless)
            # Tear down the partially-initialised side state so the next start
            # attempt doesn't trip the "already running" guard.
            if local_proxy is not None:
                with contextlib.suppress(Exception):
                    local_proxy.close()  # pyright: ignore[reportUnknownMemberType]
            if xvfb_mgr is not None:
                xvfb_mgr.cleanup()
            _clear_pid(paths)
            _clear_session(paths)
            raise
    else:
        logger.info(
            "remote_bridge_startup",
            tier=tier.value,
            note="no local browser launched; waiting for extension to connect",
        )

    from agentcloak.browser.secure_ctx import SecureBrowserContext

    ctx: Any = SecureBrowserContext(raw_ctx, cfg) if raw_ctx is not None else None

    logger.info(
        "bridge_token_loaded",
        token_suffix=bridge_token[-4:],
        persistent=True,
    )

    from agentcloak.core.discovery import register_daemon

    register_daemon(actual_port, token=bridge_token)

    from agentcloak.core.resume import ResumeWriter

    resume_writer = ResumeWriter(paths)

    # Build FastAPI app and wire runtime state.
    app = create_app()
    configure_app_state(
        app,
        browser_ctx=ctx,
        local_proxy=local_proxy,
        resume_writer=resume_writer,
        bridge_token=bridge_token,
        config=cfg,
    )
    # Seed ContextManager with whatever the startup tier created so
    # hot-switches see the live state. For remote_bridge startup the
    # local cache is empty until the user explicitly switches back.
    context_manager = ContextManager(app.state, cfg)
    context_manager.seed_initial(
        active_tier=tier,
        local_ctx=raw_ctx,
        local_tier=None if skip_local_launch else tier,
        local_profile=profile if not skip_local_launch else None,
    )
    app.state.context_manager = context_manager

    # BridgeService owns Chrome Extension WebSocket lifecycle. Created at
    # startup so route handlers (``/bridge/ws``, ``/ext``, token reset) can
    # delegate without each one re-implementing the mutex / pump loop.
    from agentcloak.daemon.services.bridge_service import BridgeService

    app.state.bridge_service = BridgeService(app.state)

    app.state.last_request_time = time.monotonic()

    logger.info("daemon_starting", host=actual_host, port=actual_port)

    uvicorn_config = uvicorn.Config(
        app,
        host=actual_host,
        port=actual_port,
        log_level="warning",
        access_log=False,
        loop="asyncio",
        ws="websockets",
    )

    try:
        server, bound_port = await _try_bind_port(
            config=uvicorn_config,
            host=actual_host,
            base_port=actual_port,
        )
    except OSError:
        logger.error(
            "all_ports_exhausted",
            range_start=actual_port,
            range_end=actual_port + _PORT_RANGE_SIZE - 1,
        )
        with contextlib.suppress(Exception):
            await context_manager.shutdown()
        _clear_pid(paths)
        _clear_session(paths)
        raise

    actual_port = bound_port

    _write_session(
        paths,
        port=actual_port,
        tier=tier,
        profile=profile,
        bridge_token=bridge_token,
    )

    logger.info("daemon_ready", host=actual_host, port=actual_port, tier=tier.value)

    resume_writer.start_background()

    # Bridge OS signals → shutdown_event so we shut down gracefully.
    # Windows ProactorEventLoop does not support add_signal_handler.
    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(
                sig,
                lambda: asyncio.ensure_future(_signal_shutdown(server, app)),
            )

    # Wire shutdown_event to stop uvicorn when /shutdown is called.
    async def _watch_shutdown_event() -> None:
        await app.state.shutdown_event.wait()
        server.should_exit = True

    background_tasks: list[asyncio.Task[Any]] = [
        asyncio.ensure_future(_watch_shutdown_event())
    ]

    if idle_timeout > 0:
        background_tasks.append(
            asyncio.ensure_future(_idle_watchdog(app, idle_timeout, server))
        )

    try:
        await server.serve()
    finally:
        logger.info("shutting_down")
        from agentcloak.core.discovery import unregister_daemon

        unregister_daemon()
        with contextlib.suppress(Exception):
            await context_manager.shutdown()
        if local_proxy is not None:
            with contextlib.suppress(Exception):
                local_proxy.close()  # pyright: ignore[reportUnknownMemberType]
        if xvfb_mgr is not None:
            xvfb_mgr.cleanup()
        resume_writer.clear()
        _clear_pid(paths)
        _clear_session(paths)


async def _idle_watchdog(app: Any, timeout: float, server: uvicorn.Server) -> None:
    """Shut down daemon after idle_timeout seconds of inactivity."""
    while not server.should_exit:
        await asyncio.sleep(30)
        elapsed = time.monotonic() - app.state.last_request_time
        if elapsed >= timeout:
            logger.info("idle_timeout_reached", seconds=int(elapsed))
            app.state.shutdown_event.set()
            return


async def _signal_shutdown(server: uvicorn.Server, app: Any) -> None:
    app.state.shutdown_event.set()
    server.should_exit = True


async def stop(*, host: str | None = None, port: int | None = None) -> bool:
    """Send shutdown request to the daemon."""
    _, cfg = load_config()
    actual_host = host or cfg.daemon.host
    actual_port = port or cfg.daemon.port
    url = f"http://{actual_host}:{actual_port}/shutdown"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(url)
    except Exception:
        pass
    return True


async def health(*, host: str | None = None, port: int | None = None) -> bool:
    """Check if daemon is reachable."""
    _, cfg = load_config()
    actual_host = host or cfg.daemon.host
    actual_port = port or cfg.daemon.port
    url = f"http://{actual_host}:{actual_port}/health"

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url)
            data = resp.json()
            return bool(data.get("ok"))
    except Exception:
        return False
