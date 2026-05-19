"""Network request monitoring command.

Exposed as a flat ``agentcloak network`` (no nested ``network network``) via
``@app.callback(invoke_without_command=True)``.
"""

from __future__ import annotations

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import render_network_text

__all__ = ["app"]

app = typer.Typer(invoke_without_command=True)


@app.callback(invoke_without_command=True)
def network_list(
    ctx: typer.Context,
    since: str = typer.Option(
        "0",
        "--since",
        help=(
            "Filter requests after this seq number. Accepts an integer or the "
            "literal token 'last_action' to fetch only requests that occurred "
            "after the most recent action."
        ),
    ),
) -> None:
    """List captured network requests."""
    # A registered subcommand (none today) would take precedence; otherwise we
    # run the callback. Keep the check so future subcommands can opt out.
    if ctx.invoked_subcommand is not None:
        return
    # ``since`` stays a string so the well-known token ``last_action`` makes it
    # through to the daemon (route handler decodes ``int`` vs literal). Numeric
    # strings are validated below so typos like ``--since notanumber`` still
    # fail fast in the CLI rather than emitting a confusing daemon error.
    if since != "last_action" and not since.lstrip("-").isdigit():
        raise typer.BadParameter(
            f"--since must be an integer or 'last_action' (got {since!r})"
        )
    dispatch_text_or_json(
        DaemonClient(),
        "GET",
        "/network",
        params={"since": str(since)},
        renderer=render_network_text,
    )
