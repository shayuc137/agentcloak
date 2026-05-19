"""Dialog commands — status, accept, dismiss."""

from __future__ import annotations

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import (
    render_dialog_handle_text,
    render_dialog_status_text,
)

__all__ = ["app"]

app = typer.Typer()


@app.command("status")
def dialog_status() -> None:
    """Check for a pending dialog."""
    dispatch_text_or_json(
        DaemonClient(), "GET", "/dialog/status", renderer=render_dialog_status_text
    )


@app.command("accept")
def dialog_accept(
    text: str | None = typer.Option(
        None, "--text", "-t", help="Reply text for prompt dialogs."
    ),
) -> None:
    """Accept the pending dialog."""
    body: dict[str, object] = {"action": "accept"}
    if text is not None:
        body["text"] = text
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/dialog/handle",
        json_body=body,
        renderer=render_dialog_handle_text,
    )


@app.command("dismiss")
def dialog_dismiss() -> None:
    """Dismiss the pending dialog."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/dialog/handle",
        json_body={"action": "dismiss"},
        renderer=render_dialog_handle_text,
    )
