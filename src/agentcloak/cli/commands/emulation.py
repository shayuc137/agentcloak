"""Emulation commands (7b T1.2) — inject extra HTTP headers.

Set headers applied to every subsequent request (custom Authorization / tokens
for API debugging). Repeat ``--header`` for multiple; run with no headers
to clear the override.
"""

from __future__ import annotations

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.cli.commands._common import parse_header_list
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import render_headers_text

__all__ = ["app"]

app = typer.Typer()


@app.command("headers")
def emulation_headers(
    header: list[str] = typer.Option(
        None,
        "--header",
        "-H",
        help="Header as 'Name: value' (repeatable). No headers clears all.",
    ),
) -> None:
    """Set (or clear) the extra HTTP headers injected on every request."""
    headers = parse_header_list(header)
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/emulation/headers",
        json_body={"headers": headers},
        renderer=render_headers_text,
    )
