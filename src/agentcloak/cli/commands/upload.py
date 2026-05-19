"""Upload command — file upload to input elements."""

from __future__ import annotations

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import render_upload_text

__all__ = ["app"]

app = typer.Typer(invoke_without_command=True)


@app.callback(invoke_without_command=True)
def do_upload(
    index: int = typer.Option(
        ..., "--index", "-i", help="Element index [N] of file input."
    ),
    file: list[str] = typer.Option(..., "--file", "-f", help="File path(s) to upload."),
) -> None:
    """Upload file(s) to a file input element."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/upload",
        json_body={"index": index, "files": file},
        renderer=render_upload_text,
    )
