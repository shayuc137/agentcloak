"""Dialog tool — handle browser dialogs (alert, confirm, prompt)."""

# pyright: reportUnusedFunction=false
# Tools register via @mcp.tool decorator side-effect.

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from agentcloak.core.text_renderers import (
    render_dialog_handle_text,
    render_dialog_status_text,
)
from agentcloak.mcp._format import format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
    async def agentcloak_dialog(
        kind: Literal["status", "accept", "dismiss"] = "status",
        text: str = "",
    ) -> str:
        """Handle browser dialogs (alert, confirm, prompt).

        alert/beforeunload dialogs are auto-accepted. confirm/prompt
        dialogs are held as pending until you call accept or dismiss.

        When a dialog is pending, all other actions return
        'blocked_by_dialog' — handle the dialog first.

        Args:
            kind: 'status' to check, 'accept' to confirm, 'dismiss' to cancel
            text: Reply text for prompt dialogs (only used with accept)

        Returns:
            JSON with dialog info (type, message) or handled status.
        """
        if kind == "status":
            return await format_call(client.dialog_status(), render_dialog_status_text)
        reply = text if text and kind == "accept" else None
        return await format_call(
            client.dialog_handle(kind, text=reply), render_dialog_handle_text
        )
