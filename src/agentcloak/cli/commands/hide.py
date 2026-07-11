"""Commands for persistent page-overlay hiding."""

from __future__ import annotations

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import render_hide_list_text, render_hide_op_text

__all__ = ["app"]

app = typer.Typer()


@app.command("add")
def hide_add(selector: str = typer.Argument(help="CSS selector to hide.")) -> None:
    """Hide a selector in the current session and active profile."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/hide/add",
        json_body={"selector": selector},
        renderer=render_hide_op_text,
    )


@app.command("remove")
def hide_remove(
    identifier_or_selector: str = typer.Argument(
        help="Identifier from hide list, or the exact CSS selector."
    ),
) -> None:
    """Remove a user-managed hide selector."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/hide/remove",
        json_body={"identifier_or_selector": identifier_or_selector},
        renderer=render_hide_op_text,
    )


@app.command("list")
def hide_list() -> None:
    """List active hide selectors and their persistence scope."""
    dispatch_text_or_json(
        DaemonClient(), "GET", "/hide/list", renderer=render_hide_list_text
    )
