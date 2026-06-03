"""Bridge lifecycle commands — doctor, claim, finalize, token.

The Chrome extension connects directly to the daemon's ``/ext`` WebSocket;
there is no longer a standalone bridge process. ``doctor`` reflects that by
probing the daemon and reporting whether the extension is currently attached.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json, emit_envelope
from agentcloak.cli.output import error, is_json_mode, value
from agentcloak.core.text_renderers import (
    render_bridge_claim_text,
    render_bridge_finalize_text,
)

__all__ = ["app"]

app = typer.Typer()


def _extension_dir() -> Path:
    bridge_root = Path(__file__).parent.parent.parent / "bridge"
    return bridge_root / "agentcloak-chrome-extension"


@app.command("doctor")
def bridge_doctor() -> None:
    """Check bridge readiness: daemon running, extension connected, files present.

    The extension attaches to the daemon's ``/ext`` WebSocket, so a healthy
    bridge means (1) the daemon answers ``/health``, (2) that health payload
    reports ``remote_connected``, and (3) the packaged extension files exist
    on disk for the user to load into Chrome.
    """
    from agentcloak.client import DaemonClient
    from agentcloak.core.errors import AgentBrowserError, DaemonConnectionError

    checks: list[dict[str, Any]] = []

    # 1. Daemon liveness + extension connection — read off a single /health.
    # ``auto_start=False`` so ``doctor`` never silently spawns a daemon just
    # to introspect it.
    daemon_running = False
    remote_connected = False
    health_detail = "daemon not reachable"
    try:
        result = DaemonClient(auto_start=False).health_sync()
        data = result.get("data", result)
        daemon_running = bool(data.get("ok", True))
        remote_connected = bool(data.get("remote_connected", False))
        health_detail = f"daemon up (v{data.get('version', '?')})"
    except (DaemonConnectionError, AgentBrowserError) as exc:
        health_detail = f"daemon not reachable ({exc.error})"

    checks.append(
        {
            "name": "daemon",
            "ok": daemon_running,
            "detail": health_detail,
            "hint": "" if daemon_running else "run: cloak daemon start -b",
        }
    )
    checks.append(
        {
            "name": "extension_connected",
            "ok": remote_connected,
            "detail": "connected" if remote_connected else "no extension attached",
            "hint": ""
            if remote_connected
            else (
                "load the extension in chrome://extensions and "
                "run 'cloak launch --tier remote_bridge'"
            ),
        }
    )

    # 2. Extension files present on disk.
    ext_dir = _extension_dir()
    manifest = ext_dir / "manifest.json"
    checks.append(
        {
            "name": "extension_files",
            "ok": manifest.is_file(),
            "detail": str(ext_dir) if manifest.is_file() else "not found",
            "hint": "" if manifest.is_file() else "extension files missing",
        }
    )

    all_ok = all(c["ok"] for c in checks)
    if is_json_mode():
        emit_envelope(
            {"ok": True, "seq": 0, "data": {"healthy": all_ok, "checks": checks}}
        )
    else:
        for check in checks:
            mark = "ok" if check["ok"] else "fail"
            line = f"[{mark}] {check['name']} | {check['detail']}"
            if check["hint"]:
                line += f" | hint: {check['hint']}"
            value(line)

    if not all_ok:
        raise typer.Exit(1)


@app.command("extension-path")
def bridge_extension_path() -> None:
    """Print the path to the Chrome extension directory."""
    ext_dir = _extension_dir()
    if is_json_mode():
        emit_envelope({"ok": True, "seq": 0, "data": {"path": str(ext_dir.resolve())}})
        return
    value(str(ext_dir.resolve()))


@app.command("claim")
def bridge_claim(
    tab_id: int | None = typer.Option(
        None, "--tab-id", help="Claim a specific tab by its Chrome tab ID."
    ),
    url: str | None = typer.Option(
        None,
        "--url",
        "--url-pattern",
        help="Claim first tab whose URL contains this substring.",
    ),
) -> None:
    """Claim a user-opened tab for agent control.

    Provide either ``--tab-id`` or ``--url``.
    """
    if tab_id is None and url is None:
        error("missing claim selector", "provide --tab-id or --url")

    from agentcloak.client import DaemonClient

    body: dict[str, Any] = {}
    if tab_id is not None:
        body["tab_id"] = tab_id
    if url is not None:
        body["url_pattern"] = url
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/bridge/claim",
        json_body=body,
        renderer=render_bridge_claim_text,
    )


@app.command("finalize")
def bridge_finalize(
    mode: str = typer.Option(
        "close",
        "--mode",
        help="Session end mode: close (default), handoff, deliverable.",
    ),
) -> None:
    """Finalize the agent session — clean up managed tabs."""
    from agentcloak.client import DaemonClient

    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/bridge/finalize",
        json_body={"mode": mode},
        # ``mode`` lives request-side — close it over so the renderer can
        # produce ``close 3 tabs`` without re-reading the request.
        renderer=lambda d: render_bridge_finalize_text(d, mode=mode),
    )


@app.command("token")
def bridge_token(
    reset: bool = typer.Option(
        False,
        "--reset",
        help="Generate a new token, replacing the persisted one.",
    ),
) -> None:
    """Show (or regenerate) the persistent bridge auth token.

    The token lives in ``~/.agentcloak/config.toml`` under ``[bridge] token``.
    Paste it into the Chrome Extension Options page to authorise the
    extension's WebSocket connection.

    ``--reset`` rotates the token; any already-paired extensions will
    have to be re-configured.
    """
    from agentcloak.client import DaemonClient
    from agentcloak.core.config import (
        ensure_bridge_token,
        load_config,
        regenerate_bridge_token,
    )
    from agentcloak.core.errors import AgentBrowserError, DaemonConnectionError

    paths, cfg = load_config()

    if reset:
        # Prefer the daemon-side reset so any already-connected extension is
        # severed on its next reconnect (close code 4001). Auto-start off:
        # silently spawning a daemon just to rotate would be surprising.
        client = DaemonClient(auto_start=False)
        new_token = ""
        hot_updated = False
        try:
            result = client.bridge_token_reset_sync()
            data = result.get("data", result)
            new_token = str(data.get("token", "") or "")
            hot_updated = bool(new_token)
        except (DaemonConnectionError, AgentBrowserError):
            new_token = ""

        if not new_token:
            new_token = regenerate_bridge_token(paths, cfg)

        if is_json_mode():
            emit_envelope(
                {
                    "ok": True,
                    "seq": 0,
                    "data": {
                        "token": new_token,
                        "action": "reset",
                        "hot_updated": hot_updated,
                        "config_file": str(paths.config_file),
                    },
                }
            )
            return
        value(new_token)
        return

    token = ensure_bridge_token(paths, cfg)
    if is_json_mode():
        emit_envelope(
            {
                "ok": True,
                "seq": 0,
                "data": {
                    "token": token,
                    "action": "show",
                    "hot_updated": False,
                    "config_file": str(paths.config_file),
                },
            }
        )
        return
    value(token)
