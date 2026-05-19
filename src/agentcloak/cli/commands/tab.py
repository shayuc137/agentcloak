"""Tab commands — list, new, close, switch."""

from __future__ import annotations

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import render_tab_list_text, render_tab_op_text

__all__ = ["app"]

app = typer.Typer()


@app.command("list")
def tab_list() -> None:
    """List all open tabs."""
    dispatch_text_or_json(DaemonClient(), "GET", "/tabs", renderer=render_tab_list_text)


@app.command("new")
def tab_new(
    url: str | None = typer.Argument(None, help="URL to navigate the new tab to."),
) -> None:
    """Create a new tab, optionally navigating to a URL."""
    body: dict[str, object] = {}
    if url:
        body["url"] = url
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/tab/new",
        json_body=body,
        # The verb in the one-liner (``new tab N | url``) is request-side
        # context, so we pre-bind it via a closure.
        renderer=lambda d: render_tab_op_text("new", d),
    )


@app.command("close")
def tab_close(
    tab_id: int = typer.Argument(help="ID of the tab to close."),
) -> None:
    """Close a tab by ID."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/tab/close",
        json_body={"tab_id": tab_id},
        renderer=lambda d: render_tab_op_text("closed", d),
    )


@app.command("switch")
def tab_switch(
    tab_id: int = typer.Argument(help="ID of the tab to switch to."),
) -> None:
    """Switch the active tab."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/tab/switch",
        json_body={"tab_id": tab_id},
        renderer=lambda d: render_tab_op_text("switched to", d),
    )
