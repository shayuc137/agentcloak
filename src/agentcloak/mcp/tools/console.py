"""Console tool — read captured console output and clear the buffer (7a R1)."""

# pyright: reportUnusedFunction=false
# Tools register via @mcp.tool decorator side-effect.

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from agentcloak.core.text_renderers import (
    render_console_clear_text,
    render_console_text,
)
from agentcloak.mcp._format import format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
    async def agentcloak_console(
        action: Literal["show", "clear"] = "show",
        since: int = 0,
        limit: int = 0,
        level: str = "",
    ) -> str:
        """Read captured browser console output, or clear the buffer.

        Console messages (console.log/warn/error/...) and uncaught page errors
        flow into a ring buffer. Use this to debug why an evaluate failed or a
        page misbehaved.

        Actions:
          show  — return buffered messages (filterable by since/limit/level)
          clear — empty the console buffer

        Args:
            action: 'show' to read, 'clear' to empty the buffer
            since: Only messages with seq greater than this (page incrementally)
            limit: Cap the number of messages returned (most recent kept)
            level: Filter to one level — log, warn, error, info, or debug

        Returns:
            show: one '[level] text (url:line)' per message plus a trailing
                'seq=N' marker to pass back as 'since'. Errors get a '!' prefix.
            clear: confirmation.
        """
        if action == "clear":
            return await format_call(client.console_clear(), render_console_clear_text)
        return await format_call(
            client.console(since=since, limit=limit, level=level),
            render_console_text,
        )
