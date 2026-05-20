"""Emulation commands (7b T1.2) — inject extra HTTP headers.

Set headers applied to every subsequent request (forged Authorization / custom
tokens for API debugging). Repeat ``--header`` for multiple; run with no headers
to clear the override.
"""

from __future__ import annotations

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.errors import AgentBrowserError
from agentcloak.core.text_renderers import render_headers_text

__all__ = ["app"]

app = typer.Typer()


def _parse_header(item: str) -> tuple[str, str]:
    """Split a ``Name: value`` header string.

    Matches the ``:`` convention used by ``cloak fetch -H`` / ``cloak graphql
    -H`` so every surface parses headers the same way (and a value containing
    ``=``, e.g. a base64 token, stays intact).
    """
    if ":" not in item:
        raise AgentBrowserError(
            error="invalid_header",
            hint=f"Header '{item}' is not 'Name: value'",
            action="pass each header as --header 'Name: value'",
        )
    name, _, value = item.partition(":")
    return name.strip(), value.strip()


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
    headers: dict[str, str] = {}
    for item in header or []:
        name, value = _parse_header(item)
        headers[name] = value
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/emulation/headers",
        json_body={"headers": headers},
        renderer=render_headers_text,
    )
