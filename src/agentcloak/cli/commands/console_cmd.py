"""Console commands — read captured console output and clear the buffer (7a R1)."""

from __future__ import annotations

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import (
    render_console_clear_text,
    render_console_text,
)

__all__ = ["app"]

app = typer.Typer()


@app.command("show")
def console_show(
    since: int = typer.Option(
        0, "--since", help="Only messages with seq greater than this value."
    ),
    limit: int = typer.Option(
        0, "--limit", help="Cap the number of messages (most recent kept)."
    ),
    level: str = typer.Option(
        "", "--level", help="Filter to one level: log, warn, error, info, debug."
    ),
    clear: bool = typer.Option(
        False, "--clear", help="Clear the console buffer instead of reading it."
    ),
) -> None:
    """Show buffered console messages and uncaught page errors."""
    client = DaemonClient()
    if clear:
        dispatch_text_or_json(
            client, "POST", "/console/clear", renderer=render_console_clear_text
        )
        return
    params: dict[str, str] = {"since": str(since)}
    if limit:
        params["limit"] = str(limit)
    if level:
        params["level"] = level
    dispatch_text_or_json(
        client, "GET", "/console", params=params, renderer=render_console_text
    )
