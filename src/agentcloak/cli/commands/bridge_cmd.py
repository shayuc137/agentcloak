"""Bridge lifecycle commands — start, doctor, claim, finalize, token."""

from __future__ import annotations

import asyncio
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


@app.command("start")
def bridge_start(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host for extension WS."),
    port: int | None = typer.Option(None, "--port", help="Bind port."),
) -> None:
    """Start the bridge process (connects extension to daemon)."""
    from agentcloak.bridge.server import start_bridge

    asyncio.run(start_bridge(host=host, port=port))


@app.command("doctor")
def bridge_doctor() -> None:
    """Check bridge component status."""
    checks: list[dict[str, Any]] = []

    # Check bridge config
    from agentcloak.bridge.config import load_bridge_config

    cfg = load_bridge_config()
    checks.append(
        {
            "name": "bridge_config",
            "ok": True,
            "detail": f"port={cfg.bridge_port}, "
            f"candidates={len(cfg.daemon_candidates)}",
            "hint": "",
        }
    )

    # Check extension directory
    ext_dir = (
        Path(__file__).parent.parent.parent / "bridge" / "agentcloak-chrome-extension"
    )
    manifest = ext_dir / "manifest.json"
    checks.append(
        {
            "name": "extension_files",
            "ok": manifest.is_file(),
            "detail": str(ext_dir) if manifest.is_file() else "not found",
            "hint": "" if manifest.is_file() else "extension files missing",
        }
    )

    # Check the WS toolchain — Starlette (server) + websockets (client).
    for pkg_name in ("starlette", "websockets", "uvicorn"):
        try:
            mod = __import__(pkg_name)
            checks.append(
                {
                    "name": pkg_name,
                    "ok": True,
                    "detail": getattr(mod, "__version__", "unknown"),
                    "hint": "",
                }
            )
        except ImportError:
            checks.append(
                {
                    "name": pkg_name,
                    "ok": False,
                    "detail": "not installed",
                    "hint": f"pip install {pkg_name}",
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
    ext_dir = (
        Path(__file__).parent.parent.parent / "bridge" / "agentcloak-chrome-extension"
    )
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
