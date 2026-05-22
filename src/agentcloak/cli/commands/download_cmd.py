"""Download commands — direct-URL download, click-triggered wait, listing (7a R2).

The daemon performs the download and saves the file on the daemon host
(defaulting to the system temp dir), so these commands pass the target
directory through and render the saved path the daemon reports back.
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — Typer needs runtime access

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import (
    render_download_list_text,
    render_download_text,
)

__all__ = ["app"]

app = typer.Typer()


@app.command("url")
def download_url(
    url: str = typer.Argument(..., help="http(s) URL to download."),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Directory to save into (default: system temp dir).",
    ),
) -> None:
    """Download a URL directly (server-side, with the browser's cookies)."""
    body: dict[str, object] = {"url": url}
    if output is not None:
        body["output_dir"] = str(output)
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/download/url",
        json_body=body,
        renderer=render_download_text,
    )


@app.command("wait")
def download_wait(
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Directory to save into (default: system temp dir).",
    ),
    timeout: float | None = typer.Option(
        None, "--timeout", help="Seconds to wait for a download to start."
    ),
) -> None:
    """Wait for the next click-triggered download and save it."""
    body: dict[str, object] = {}
    if output is not None:
        body["output_dir"] = str(output)
    if timeout is not None:
        body["timeout"] = timeout
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/download/wait",
        json_body=body,
        renderer=render_download_text,
    )


@app.command("wait-click")
def download_wait_click(
    index: int = typer.Option(
        ..., "--index", "-i", help="Element [N] to click (the download trigger)."
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Directory to save into (default: system temp dir).",
    ),
    timeout: float | None = typer.Option(
        None, "--timeout", help="Seconds to wait for the download."
    ),
    force: bool = typer.Option(
        False, "--force", help="Skip pointer check on the click."
    ),
) -> None:
    """Click an element and wait for the triggered download to complete."""
    body: dict[str, object] = {"index": index, "force": force}
    if output is not None:
        body["output_dir"] = str(output)
    if timeout is not None:
        body["timeout"] = timeout
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/download/wait-click",
        json_body=body,
        renderer=render_download_text,
    )


@app.command("list")
def download_list() -> None:
    """List downloads saved during this session."""
    dispatch_text_or_json(
        DaemonClient(), "GET", "/download/list", renderer=render_download_list_text
    )
