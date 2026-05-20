"""Shared helpers for CLI command modules."""

from __future__ import annotations

from agentcloak.core.errors import AgentBrowserError

__all__ = ["parse_header_list"]


def parse_header_list(items: list[str] | None) -> dict[str, str]:
    """Parse repeated ``--header 'Name: value'`` options into a dict.

    Splits on the first ``:`` only, so a value that itself contains ``:`` or
    ``=`` (a URL, a base64 token) survives intact. ``None``/empty yields an
    empty dict, which ``emulation headers`` treats as "clear all overrides".

    Shared by the ``emulation`` and ``graphql`` commands so every ``-H`` flag
    parses the same way and raises one consistent ``invalid_header`` error.
    (``fetch`` keeps its own parser because it runs inside a Typer callback and
    surfaces usage errors via ``typer.BadParameter`` rather than the structured
    envelope used here.)
    """
    headers: dict[str, str] = {}
    for item in items or []:
        name, sep, value = item.partition(":")
        if not sep:
            raise AgentBrowserError(
                error="invalid_header",
                hint=f"Header '{item}' is not 'Name: value'",
                action="pass each header as --header 'Name: value'",
            )
        headers[name.strip()] = value.strip()
    return headers
