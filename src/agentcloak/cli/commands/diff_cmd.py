"""Local screenshot comparison commands."""

from __future__ import annotations

import base64
import binascii
from pathlib import Path  # noqa: TC003 - Typer resolves command annotations at runtime.

import typer

from agentcloak.cli._dispatch import emit_envelope
from agentcloak.cli.output import error_from_exception, is_json_mode, value
from agentcloak.client import DaemonClient
from agentcloak.core.errors import AgentBrowserError, ImageDiffError
from agentcloak.core.image_diff import compare_images
from agentcloak.core.text_renderers import render_image_diff_text

__all__ = ["app", "diff_screenshot"]

app = typer.Typer()


def _live_png() -> tuple[bytes, int]:
    result = DaemonClient().screenshot_sync(format="png")
    data = result.get("data", result)
    encoded = str(data.get("base64", "") or "")
    if not encoded:
        raise ImageDiffError(
            error="image_diff_capture_empty",
            hint="Live PNG capture returned no image bytes",
            action="retry after confirming the browser has a valid page",
        )
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageDiffError(
            error="image_diff_capture_invalid",
            hint=f"Live PNG capture returned invalid base64: {exc}",
            action="retry, or check daemon logs",
        ) from exc
    return decoded, int(result.get("seq", 0) or 0)


@app.command("screenshot")
def diff_screenshot(
    baseline: Path = typer.Argument(..., help="Local baseline image path."),
    current: Path | None = typer.Option(
        None,
        "--current",
        help="Local current image path; omitted captures the live page as PNG.",
    ),
    threshold: int = typer.Option(
        0,
        "--threshold",
        help="Ignore channel deltas at or below this value (0-255).",
    ),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Write a red-highlight difference image."
    ),
) -> None:
    """Compare a baseline screenshot with a local image or the live page."""
    try:
        seq = 0
        if current is None:
            current_source, seq = _live_png()
            current_label = "<live-page>"
        else:
            current_source = current.expanduser()
            current_label = str(current_source.resolve())

        baseline_path = baseline.expanduser()
        result = compare_images(
            baseline_path,
            current_source,
            threshold=threshold,
            output=output,
        )
        data = result.to_dict()
        data.update(
            {
                "baseline": str(baseline_path.resolve()),
                "current": current_label,
                "output": str(output.expanduser().resolve()) if output else None,
            }
        )
    except AgentBrowserError as exc:
        error_from_exception(exc)
        return

    if is_json_mode():
        emit_envelope({"ok": True, "seq": seq, "data": data})
        return
    value(render_image_diff_text(data))
