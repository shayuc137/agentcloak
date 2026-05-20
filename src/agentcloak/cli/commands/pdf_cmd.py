"""PDF command — export the current page to a PDF file (7a R6).

Mirrors ``screenshot``: the daemon returns the PDF bytes base64-encoded and
the CLI decodes them and writes the file locally (so it works against a remote
daemon too). Without ``--output`` the file lands in the system temp dir and
the path is printed.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from tempfile import gettempdir

import typer

from agentcloak.cli._dispatch import emit_envelope
from agentcloak.cli.output import error, is_json_mode, value
from agentcloak.client import DaemonClient

__all__ = ["app", "pdf"]

app = typer.Typer()


@app.callback(invoke_without_command=True)
def pdf(
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Save to a specific file. Default: <system-temp>/agentcloak-<ts>.pdf.",
    ),
    page_format: str = typer.Option(
        "A4", "--format", "-f", help="Paper format: A4, Letter, Legal, etc."
    ),
    landscape: bool = typer.Option(False, "--landscape", help="Landscape orientation."),
    scale: float | None = typer.Option(None, "--scale", help="Render scale (0.1-2.0)."),
    page_ranges: str = typer.Option(
        "", "--pages", help="Pages to print, e.g. '1-3,5' (default: all)."
    ),
) -> None:
    """Export the current page to a PDF (headless Chromium only)."""
    client = DaemonClient()
    # Pull the JSON envelope (base64 payload) so we can write the file locally.
    result = client.pdf_sync(
        format=page_format,
        landscape=landscape,
        scale=scale,
        page_ranges=page_ranges or None,
    )
    data = result.get("data", result)
    seq = int(result.get("seq", 0) or 0)

    b64_str: str = data.get("base64", "")
    if not b64_str:
        if is_json_mode():
            emit_envelope(result)
            return
        error("pdf returned empty payload", "retry, or check daemon logs")
        return

    if output is None:
        ts = int(time.time() * 1000)
        output = Path(gettempdir()) / f"agentcloak-{ts}.pdf"

    output.write_bytes(base64.b64decode(b64_str))

    if is_json_mode():
        emit_envelope(
            {
                "ok": True,
                "seq": seq,
                "data": {"saved": str(output), "size": data.get("size", 0)},
            }
        )
        return
    value(str(output))
