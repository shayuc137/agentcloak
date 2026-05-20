"""Serve commands — local static file server for previewing local files (7a R7).

``file://`` navigations are blocked by the security layer, so ``cloak serve
start <dir>`` exposes a directory over http://127.0.0.1:<port> instead. The
server is localhost-only and is stopped automatically when the daemon exits.
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — Typer needs runtime access

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import (
    render_serve_status_text,
    render_serve_stop_text,
)

__all__ = ["app"]

app = typer.Typer()


@app.command("start")
def serve_start(
    directory: Path = typer.Argument(..., help="Local directory to serve."),
    port: int | None = typer.Option(
        None, "--port", "-p", help="Preferred port (default: auto-allocate)."
    ),
) -> None:
    """Start a localhost static file server rooted at a directory."""
    body: dict[str, object] = {"directory": str(directory)}
    if port is not None:
        body["port"] = port
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/serve/start",
        json_body=body,
        renderer=render_serve_status_text,
    )


@app.command("stop")
def serve_stop() -> None:
    """Stop the running file server."""
    dispatch_text_or_json(
        DaemonClient(), "POST", "/serve/stop", renderer=render_serve_stop_text
    )


@app.command("status")
def serve_status() -> None:
    """Show the file server status."""
    dispatch_text_or_json(
        DaemonClient(), "GET", "/serve/status", renderer=render_serve_status_text
    )
