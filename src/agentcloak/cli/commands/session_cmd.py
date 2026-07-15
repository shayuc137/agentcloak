"""Session management commands — list and close named sessions."""

from __future__ import annotations

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import render_session_list_text

__all__ = ["app"]

app = typer.Typer()


@app.command("list")
def session_list() -> None:
    """List all named sessions and their state."""
    dispatch_text_or_json(
        DaemonClient(),
        "GET",
        "/session/list",
        renderer=render_session_list_text,
    )


@app.command("close")
def session_close(
    session_id: str = typer.Argument(
        None, help="Session id to close (omit to close the default session)."
    ),
) -> None:
    """Close a named session and release its browser.

    When called without an argument, closes the default (unnamed) session's
    browser without stopping the daemon.
    """
    body: dict[str, str] = {}
    if session_id:
        body["session_id"] = session_id
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/session/close",
        json_body=body,
        renderer=render_session_list_text,
    )
