"""Clipboard tool — read and write the system clipboard (7a R5)."""

# pyright: reportUnusedFunction=false
# Tools register via @mcp.tool decorator side-effect.

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from agentcloak.core.text_renderers import (
    render_clipboard_read_text,
    render_clipboard_write_text,
)
from agentcloak.mcp._format import format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
    async def agentcloak_clipboard(
        action: Literal["read", "write"] = "read",
        text: str = "",
    ) -> str:
        """Read or write the system clipboard.

        Actions:
          read  — return the current clipboard text
          write — set the clipboard to 'text'

        Permission (clipboard-read / clipboard-write) is granted automatically
        before the access.

        Args:
            action: 'read' or 'write'
            text: Text to copy (required for 'write')

        Returns:
            read: the clipboard text.
            write: 'wrote N chars to clipboard'.
        """
        if action == "write":
            return await format_call(
                client.clipboard_write(text=text), render_clipboard_write_text
            )
        return await format_call(client.clipboard_read(), render_clipboard_read_text)
