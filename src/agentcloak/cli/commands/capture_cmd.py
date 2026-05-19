"""Capture commands — record, export, and analyze network traffic."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — Typer needs runtime access

import orjson
import typer

from agentcloak.cli._dispatch import dispatch_text_or_json, emit_envelope
from agentcloak.cli.output import is_json_mode, value
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import (
    render_capture_analyze_text,
    render_capture_clear_text,
    render_capture_export_text,
    render_capture_status_text,
    render_fetch_text,
)

__all__ = ["app"]

app = typer.Typer()


@app.command("start")
def capture_start() -> None:
    """Start recording network traffic."""
    dispatch_text_or_json(
        DaemonClient(), "POST", "/capture/start", renderer=render_capture_status_text
    )


@app.command("stop")
def capture_stop() -> None:
    """Stop recording network traffic."""
    dispatch_text_or_json(
        DaemonClient(), "POST", "/capture/stop", renderer=render_capture_status_text
    )


@app.command("status")
def capture_status() -> None:
    """Show capture recording status."""
    dispatch_text_or_json(
        DaemonClient(), "GET", "/capture/status", renderer=render_capture_status_text
    )


@app.command("export")
def capture_export(
    format: str = typer.Option("har", help="Export format: har or json."),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write the export to a file instead of stdout (HAR can be large).",
    ),
) -> None:
    """Export captured traffic as HAR or JSON."""
    client = DaemonClient()
    if output is not None:
        # File output always uses the structured envelope so we can write the
        # raw HAR/JSON regardless of CLI mode.
        result = client.capture_export_sync(fmt=format)
        data = result.get("data", result)
        output.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))
        if is_json_mode():
            seq = int(result.get("seq", 0) or 0)
            emit_envelope(
                {
                    "ok": True,
                    "seq": seq,
                    "data": {
                        "saved": str(output),
                        "format": format,
                        "bytes": output.stat().st_size,
                    },
                }
            )
            return
        value(f"saved {output} ({output.stat().st_size} bytes)")
        return

    dispatch_text_or_json(
        client,
        "GET",
        "/capture/export",
        params={"format": format},
        renderer=render_capture_export_text,
    )


@app.command("analyze")
def capture_analyze(
    domain: str = typer.Option("", help="Filter by domain."),
) -> None:
    """Analyze captured traffic for API patterns."""
    params: dict[str, str] = {}
    if domain:
        params["domain"] = domain
    dispatch_text_or_json(
        DaemonClient(),
        "GET",
        "/capture/analyze",
        params=params,
        renderer=render_capture_analyze_text,
    )


@app.command("clear")
def capture_clear() -> None:
    """Clear all captured traffic data."""
    dispatch_text_or_json(
        DaemonClient(), "POST", "/capture/clear", renderer=render_capture_clear_text
    )


@app.command("replay")
def capture_replay(
    url: str = typer.Argument(..., help="URL of the request to replay."),
    method: str = typer.Option("GET", "--method", "-m", help="HTTP method."),
) -> None:
    """Replay the most recent captured request matching url+method."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/capture/replay",
        json_body={"url": url, "method": method},
        # Replay returns an HTTP-style response; ``render_fetch_text`` emits
        # the body bare (or a status line when the body is empty), matching
        # the pre-refactor daemon path.
        renderer=render_fetch_text,
    )
