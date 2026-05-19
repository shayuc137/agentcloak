"""Network tool — request monitoring."""

# pyright: reportUnusedFunction=false
# Tools register via @mcp.tool decorator side-effect.

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.types import ToolAnnotations

from agentcloak.core.text_renderers import render_network_text
from agentcloak.mcp._format import format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def agentcloak_network(since: str = "0") -> str:
        """List captured network requests since a given seq number.

        Use since='last_action' to see requests triggered by the most recent
        action (click, navigate, etc.).

        Args:
            since: Seq number to filter from, or 'last_action' for latest

        Returns:
            JSON with requests array (method, url, status, resource_type) and count.
        """
        return await format_call(client.network(since=since), render_network_text)
