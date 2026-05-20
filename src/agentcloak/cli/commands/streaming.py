"""Streaming commands (7b T2) — capture WebSocket frames and SSE events.

Two command groups share this file because they're the same capability seen
from two CDP event families:

* ``ws list``            — tracked WebSocket connections
* ``ws messages --since`` — buffered WebSocket frames (seq/since paging)
* ``sse messages --since`` — buffered Server-Sent Events (seq/since paging)

Capture turns on lazily on the first ``messages``/``list`` call (the daemon
enables the CDP ``Network`` domain once), so an agent that never inspects
streaming traffic never pays for it.
"""

from __future__ import annotations

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import (
    render_sse_messages_text,
    render_ws_list_text,
    render_ws_messages_text,
)

__all__ = ["sse_app", "ws_app"]

ws_app = typer.Typer()
sse_app = typer.Typer()


@ws_app.command("list")
def ws_list() -> None:
    """List tracked WebSocket connections (cleared on navigation)."""
    dispatch_text_or_json(
        DaemonClient(), "GET", "/ws/list", renderer=render_ws_list_text
    )


@ws_app.command("messages")
def ws_messages(
    since: int = typer.Option(
        0, "--since", help="Only frames with seq greater than this value."
    ),
) -> None:
    """Show buffered WebSocket frames (→ sent, ← received)."""
    dispatch_text_or_json(
        DaemonClient(),
        "GET",
        "/ws/messages",
        params={"since": str(since)},
        renderer=render_ws_messages_text,
    )


@sse_app.command("messages")
def sse_messages(
    since: int = typer.Option(
        0, "--since", help="Only events with seq greater than this value."
    ),
) -> None:
    """Show buffered Server-Sent Events (EventSource messages)."""
    dispatch_text_or_json(
        DaemonClient(),
        "GET",
        "/sse/messages",
        params={"since": str(since)},
        renderer=render_sse_messages_text,
    )
