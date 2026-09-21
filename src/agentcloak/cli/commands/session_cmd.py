"""Session management commands — list and close named sessions."""

from __future__ import annotations

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import render_session_list_text

__all__ = ["app"]

app = typer.Typer()


@app.command("list")
def session_list(
    all_workspaces: bool = typer.Option(
        False, "--all", help="Include all workspaces and paths."
    ),
) -> None:
    """List all named sessions and their state."""
    dispatch_text_or_json(
        DaemonClient(),
        "GET",
        "/session/list",
        params={"all_workspaces": "true"} if all_workspaces else None,
        renderer=render_session_list_text,
    )


@app.command("close")
def session_close(
    force: bool = typer.Option(
        False,
        "--force",
        help="Cancel pending actions and close this session immediately.",
    ),
    session_id: str = typer.Argument(
        None, help="Session id to close (omit for the caller session)."
    ),
) -> None:
    """Close the caller or named session without stopping the shared daemon."""
    body: dict[str, object] = {"force": force}
    if session_id:
        body["session_id"] = session_id
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/session/close",
        json_body=body,
        renderer=render_session_list_text,
    )
