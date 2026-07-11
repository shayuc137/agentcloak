"""Persistent page-overlay hiding tool."""

# pyright: reportUnusedFunction=false

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from agentcloak.core.text_renderers import render_hide_list_text, render_hide_op_text
from agentcloak.mcp._format import format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
    async def agentcloak_hide(
        action: Literal["add", "remove", "list"] = "list",
        selector: str = "",
        identifier_or_selector: str = "",
    ) -> str:
        """Manage CSS selectors hidden across navigation in the browser session.

        Args:
            action: add, remove, or list.
            selector: CSS selector for add.
            identifier_or_selector: Stable id or exact selector for remove.

        Returns:
            Operation confirmation or one active selector per line.
        """
        if action == "add":
            return await format_call(
                client.hide_add(selector=selector), render_hide_op_text
            )
        if action == "remove":
            return await format_call(
                client.hide_remove(identifier_or_selector=identifier_or_selector),
                render_hide_op_text,
            )
        return await format_call(client.hide_list(), render_hide_list_text)
