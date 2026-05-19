"""Daemon lifecycle commands — start, stop, status, cdp-endpoint."""

from __future__ import annotations

import asyncio
import contextlib
import time

import httpx
import typer

from agentcloak.cli._dispatch import dispatch_text_or_json, emit_envelope
from agentcloak.cli.output import error_from_exception, is_json_mode, value
from agentcloak.core.errors import DaemonConnectionError
from agentcloak.core.text_renderers import (
    render_cdp_endpoint_text,
    render_health_text,
    render_shutdown_text,
)

__all__ = ["app"]

app = typer.Typer()

# When ``daemon start -b`` returns, the daemon is launched but uvicorn is still
# binding the socket. The first subsequent CLI/MCP request then races the
# spawn and triggers the auto-start path's "daemon_auto_starting" warning. Poll
# /health for a short budget so we only return once the daemon is reachable.
_BG_READY_BUDGET_S = 3.0
_BG_READY_POLL_INTERVAL_S = 0.2
_BG_READY_PROBE_TIMEOUT_S = 0.5


def _wait_for_daemon_ready(base_url: str) -> bool:
    """Poll ``GET /health`` until 200 or budget elapses. Returns ``True`` on success."""
    deadline = time.monotonic() + _BG_READY_BUDGET_S
    while time.monotonic() < deadline:
        try:
            with httpx.Client(
                base_url=base_url, timeout=_BG_READY_PROBE_TIMEOUT_S
            ) as client:
                resp = client.get("/health")
                if resp.status_code == 200:
                    return True
        except httpx.HTTPError:
            pass
        time.sleep(_BG_READY_POLL_INTERVAL_S)
    return False


@app.command("start")
def daemon_start(
    background: bool = typer.Option(
        False, "--background", "-b", help="Run in background."
    ),
    headless: bool | None = typer.Option(
        None, "--headless/--headed", help="Headless mode (default: config)."
    ),
    host: str | None = typer.Option(None, "--host", help="Bind host."),
    port: int | None = typer.Option(None, "--port", help="Bind port."),
    profile: str | None = typer.Option(
        None, "--profile", "-p", help="Browser profile name."
    ),
    humanize: bool = typer.Option(
        False, "--humanize", help="Enable humanize behavioral layer."
    ),
    no_humanize: bool = typer.Option(
        False,
        "--no-humanize",
        help="Explicitly disable humanize layer.",
        hidden=True,
    ),
) -> None:
    """Start the agentcloak daemon."""
    resolved_humanize: bool | None = None
    if humanize:
        resolved_humanize = True
    elif no_humanize:
        resolved_humanize = False

    if background:
        from agentcloak.client import DaemonClient
        from agentcloak.core.config import load_config, resolve_tier

        client = DaemonClient(host=host, port=port, auto_start=False)
        pid = client.spawn_background(
            host=host,
            port=port,
            headless=headless,
            profile=profile,
            humanize=resolved_humanize,
        )
        _, cfg = load_config()
        resolved_tier = resolve_tier(cfg.default_tier)

        # Block until daemon is reachable so subsequent CLI/MCP requests don't
        # race the spawn and trigger the auto-start warning path.
        bind_host = host or cfg.daemon_host
        bind_port = port or cfg.daemon_port
        ready = _wait_for_daemon_ready(f"http://{bind_host}:{bind_port}")

        if is_json_mode():
            emit_envelope(
                {
                    "ok": True,
                    "seq": 0,
                    "data": {
                        "pid": pid,
                        "background": True,
                        "profile": profile,
                        "tier": resolved_tier,
                        "ready": ready,
                    },
                }
            )
            return
        ready_str = "ready" if ready else "starting"
        line = (
            f"started bg | pid {pid} | http://{bind_host}:{bind_port} "
            f"| tier={resolved_tier} | {ready_str}"
        )
        value(line)
        return

    from agentcloak.daemon.server import start

    asyncio.run(
        start(
            host=host,
            port=port,
            headless=headless,
            profile=profile,
            humanize=resolved_humanize,
        )
    )


@app.command("stop")
def daemon_stop(
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """Stop the running daemon."""
    from agentcloak.client import DaemonClient

    client = DaemonClient(host=host, port=port, auto_start=False)
    if is_json_mode():
        client.shutdown_sync()
        emit_envelope({"ok": True, "seq": 0, "data": {"stopped": True}})
        return
    # Text path tolerates the daemon-already-gone case (shutdown drops the
    # listener mid-handler so the response is often empty/connection-reset).
    # We still invoke shutdown for its side effect and emit the fixed
    # ``stopped`` token from the shared renderer so CLI/MCP stay aligned.
    with contextlib.suppress(DaemonConnectionError):
        client.shutdown_sync()
    value(render_shutdown_text({}))


@app.command("cdp-endpoint")
def daemon_cdp_endpoint(
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """Get the CDP WebSocket URL for the current browser."""
    from agentcloak.client import DaemonClient

    dispatch_text_or_json(
        DaemonClient(host=host, port=port),
        "GET",
        "/cdp/endpoint",
        renderer=render_cdp_endpoint_text,
    )


@app.command("status")
def daemon_status(
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """Show daemon status (tier, browser status, seq, current URL, capture state).

    The HTTP endpoint is still ``/health`` (industry convention for monitoring
    + Kubernetes-style liveness probes); the CLI command is named ``status``
    so it matches the systemctl/docker/kubectl vocabulary agents already know.
    """
    from agentcloak.client import DaemonClient

    client = DaemonClient(host=host, port=port, auto_start=False)
    try:
        result = client.health_sync()
        # /health is a flat dict (no OkEnvelope wrap); strip ``ok`` for the
        # JSON path so we don't double-wrap, and feed the same dict to the
        # text renderer for symmetric CLI/MCP output.
        if is_json_mode():
            seq = int(result.get("seq", 0) or 0)
            payload = {k: v for k, v in result.items() if k != "ok"}
            emit_envelope({"ok": True, "seq": seq, "data": payload})
            return
        value(render_health_text(result))
    except DaemonConnectionError as e:
        error_from_exception(e)
