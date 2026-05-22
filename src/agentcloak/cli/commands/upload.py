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
    file: list[str] = typer.Option(..., "--file", "-f", help="File path(s) to upload."),
    index: int | None = typer.Option(
        None,
        "--index",
        "-i",
        help="Element index [N] of file input. Omit to auto-find hidden inputs.",
    ),
    nth: int = typer.Option(
        0,
        "--nth",
        help="When auto-finding (no --index), pick the nth file input (0-based).",
    ),
) -> None:
    """Upload file(s) to a file input element.

    With ``--index`` it targets a specific snapshot ref. Without it, the daemon
    auto-finds ``input[type=file]`` elements (including ``display:none`` ones
    drag-drop uploaders use) and attaches to the ``--nth`` one.
    """
    body: dict[str, object] = {"files": file, "nth": nth}
    if index is not None:
        body["index"] = index
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/upload",
        json_body=body,
        renderer=render_upload_text,
    )
