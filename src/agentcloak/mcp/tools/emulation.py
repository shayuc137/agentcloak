"""Emulation tool (7b T1.2) — inject extra HTTP headers."""

# pyright: reportUnusedFunction=false
# Tools register via @mcp.tool decorator side-effect.

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.types import ToolAnnotations

from agentcloak.core.text_renderers import (
    render_cdp_send_text,
    render_headers_text,
    render_viewport_text,
)
from agentcloak.mcp._format import format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
    async def agentcloak_headers(headers: dict[str, str] | None = None) -> str:
        """Set extra HTTP headers applied to every subsequent request.

        Use to forge an Authorization token, X-Requested-With, or any custom
        header while debugging an API. The headers persist until replaced;
        call with no headers (or an empty map) to clear the override.

        Args:
            headers: Header name → value map. Empty/None clears all overrides.

        Returns:
            A short confirmation naming the active headers (or 'cleared').
        """
        return await format_call(
            client.emulation_headers(headers=headers or {}), render_headers_text
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
    async def agentcloak_viewport(width: int, height: int) -> str:
        """Resize the current session page without reloading it."""
        return await format_call(
            client.viewport(width=width, height=height), render_viewport_text
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
    async def agentcloak_cdp_send(
        method: str, params: dict[str, Any] | None = None, timeout: int = 30000
    ) -> str:
        """Send a CDP command to this session's page; timeout is milliseconds."""
        return await format_call(
            client.cdp_send(method=method, params=params, timeout=timeout),
            render_cdp_send_text,
        )
