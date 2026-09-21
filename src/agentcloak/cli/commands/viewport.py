"""Per-session viewport sizing."""

from __future__ import annotations

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.input import parse_viewport
from agentcloak.core.text_renderers import render_viewport_text

app = typer.Typer()


@app.command("set")
def viewport_set(
    size: str = typer.Argument(..., help="WIDTHxHEIGHT, e.g. 1920x1080."),
) -> None:
    """Resize the current page without navigating or restarting the browser."""
    width, height = parse_viewport(size)
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/viewport",
        json_body={"width": width, "height": height},
        renderer=render_viewport_text,
    )
