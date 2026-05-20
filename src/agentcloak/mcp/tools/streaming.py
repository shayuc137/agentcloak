"""Streaming tool (7b T2) — capture WebSocket frames and SSE events."""

# pyright: reportUnusedFunction=false
# Tools register via @mcp.tool decorator side-effect.

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from agentcloak.core.text_renderers import (
    render_sse_messages_text,
    render_ws_list_text,
    render_ws_messages_text,
)
from agentcloak.mcp._format import format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def agentcloak_streaming(
        action: Literal["ws_list", "ws_messages", "sse_messages"] = "ws_messages",
        since: int = 0,
    ) -> str:
        """Capture WebSocket frames and Server-Sent Events.

        Streaming traffic is invisible to the ordinary network view — this taps
        the CDP Network domain for WebSocket frames and EventSource messages.
        Capture turns on the first time you call this (no setup needed); frames
        and events land in ring buffers paged by a monotonic seq, just like the
        console. Pass the returned 'seq' back as 'since' to read only new ones.

        Actions:
          ws_list      — tracked WebSocket connections (cleared on navigation)
          ws_messages  — buffered WebSocket frames (use 'since' to page)
          sse_messages — buffered Server-Sent Events (use 'since' to page)

        Args:
            action: 'ws_list', 'ws_messages', or 'sse_messages'
            since: Only return frames/events with seq greater than this value

        Returns:
            ws_list: one '<status> <url> (<request_id>)' per line.
            ws_messages: one '<seq> <→|←> <payload>' per line (→ sent, ← recv).
            sse_messages: one '<seq> [<event>] <data>' per line.
        """
        if action == "ws_list":
            return await format_call(client.ws_list(), render_ws_list_text)
        if action == "sse_messages":
            return await format_call(
                client.sse_messages(since=since), render_sse_messages_text
            )
        return await format_call(
            client.ws_messages(since=since), render_ws_messages_text
        )
