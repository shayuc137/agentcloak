"""CDP commands — endpoint."""

from __future__ import annotations

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import render_cdp_endpoint_text

__all__ = ["app"]

app = typer.Typer()


@app.command("endpoint")
def cdp_endpoint() -> None:
    """Get the CDP WebSocket endpoint URL for jshookmcp browser_attach."""
    dispatch_text_or_json(
        DaemonClient(), "GET", "/cdp/endpoint", renderer=render_cdp_endpoint_text
    )
