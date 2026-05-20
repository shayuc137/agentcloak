"""Clipboard commands — read and write the system clipboard (7a R5)."""

from __future__ import annotations

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import (
    render_clipboard_read_text,
    render_clipboard_write_text,
)

__all__ = ["app"]

app = typer.Typer()


@app.command("read")
def clipboard_read() -> None:
    """Read the system clipboard text."""
    dispatch_text_or_json(
        DaemonClient(), "GET", "/clipboard/read", renderer=render_clipboard_read_text
    )


@app.command("write")
def clipboard_write(
    text: str = typer.Argument(..., help="Text to copy to the clipboard."),
) -> None:
    """Write text to the system clipboard."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/clipboard/write",
        json_body={"text": text},
        renderer=render_clipboard_write_text,
    )
