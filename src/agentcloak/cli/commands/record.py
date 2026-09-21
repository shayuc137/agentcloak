"""Session screen recording (distinct from network capture)."""

import base64
from pathlib import Path
from tempfile import gettempdir
from uuid import uuid4

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json, emit_envelope
from agentcloak.cli.output import is_json_mode, value
from agentcloak.client import DaemonClient
from agentcloak.core.input import invalid_input
from agentcloak.core.text_renderers import render_record_text

app = typer.Typer()


@app.command("start")
def record_start(
    format: str = typer.Option(
        "webm", "--format", help="webm (ffmpeg required) or zip frame archive."
    ),
    max_frames: int = typer.Option(600, "--max-frames", min=1, max=3000),
    max_seconds: int = typer.Option(120, "--max-seconds", min=1, max=600),
) -> None:
    """Record the current tab, pinned until stop or a recording limit."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/record/start",
        json_body={
            "format": format,
            "max_frames": max_frames,
            "max_seconds": max_seconds,
        },
        renderer=render_record_text,
    )


@app.command("status")
def record_status() -> None:
    """Inspect recording limits and frame count."""
    dispatch_text_or_json(
        DaemonClient(), "GET", "/record/status", renderer=render_record_text
    )


@app.command("stop")
def record_stop(
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Local artifact destination."
    ),
) -> None:
    """Stop and save the recording; default destination is a temporary file."""
    if output is not None and not output.expanduser().parent.is_dir():
        raise invalid_input(
            f"Recording parent directory does not exist: {output.parent}"
        )
    result = DaemonClient()._send_sync("POST", "/record/stop")  # pyright: ignore[reportPrivateUsage]
    data = result["data"]
    output = (
        output.expanduser()
        if output
        else Path(gettempdir())
        / f"agentcloak-record-{uuid4().hex[:8]}.{data['format']}"
    )
    output.write_bytes(base64.b64decode(data.pop("base64")))
    data["saved"] = str(output)
    if is_json_mode():
        emit_envelope(result)
    else:
        value(render_record_text(data))
