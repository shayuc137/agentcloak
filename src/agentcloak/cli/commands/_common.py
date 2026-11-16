"""Shared helpers for CLI command modules."""

from __future__ import annotations

from typing import Any, Literal

import typer

from agentcloak.core.errors import AgentBrowserError
from agentcloak.core.input import parse_duration

__all__ = ["duration_option", "parse_header_list"]


def duration_option(
    default: Any,
    flag: str,
    *,
    unit: Literal["s", "ms"],
    help: str,
    minimum: float = 0,
) -> Any:
    """A timeout option taking ``45s`` / ``45000ms`` or a bare number of ``unit``.

    Every timeout flag goes through here so the accepted spellings and the
    wrong-unit warning are the same everywhere. Bare numbers keep the flag's
    historical unit; one that looks like the other unit (``wait --timeout 45``,
    ``navigate --timeout 45000``) still runs but warns on stderr.
    """
    unit_word = "seconds" if unit == "s" else "milliseconds"

    def parse(raw: Any) -> float | int:
        # Click also feeds non-None defaults (e.g. cdp's 30000) through here.
        raw = str(raw).strip()
        try:
            amount, explicit = parse_duration(raw, unit=unit)
        except AgentBrowserError as exc:
            raise typer.BadParameter(exc.hint) from exc
        if amount < minimum:
            raise typer.BadParameter(f"{flag} must be at least {minimum:g}{unit}")
        if not explicit and (
            (unit == "ms" and 0 < amount < 1000) or (unit == "s" and amount >= 1000)
        ):
            other = "s" if unit == "ms" else "ms"
            typer.echo(
                f"warning: {flag} {raw} means {raw}{unit}; "
                f"write {raw}{other} if you meant "
                f"{'seconds' if other == 's' else 'milliseconds'}",
                err=True,
            )
        return round(amount) if unit == "ms" else amount

    return typer.Option(
        default,
        flag,
        parser=parse,
        metavar="DURATION",
        help=f"{help} Bare numbers are {unit_word}; 45s / 45000ms also work.",
    )


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
