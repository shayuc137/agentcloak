"""Frame tool — frame listing and switching."""

# pyright: reportUnusedFunction=false
# Tools register via @mcp.tool decorator side-effect.

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from agentcloak.core.text_renderers import (
    render_frame_focus_text,
    render_frame_list_text,
)
from agentcloak.mcp._format import format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
    async def agentcloak_frame(
        kind: Literal["list", "focus"] = "list",
        name: str = "",
        url: str = "",
        main: bool = False,
    ) -> str:
        """List or switch between page frames (iframes).

        After switching frames, actions and snapshots operate within
        the focused frame. Use kind='focus' with main=True to return
        to the main frame.

        Args:
            kind: 'list' to show all frames, 'focus' to switch
            name: Frame name to switch to (for focus)
            url: URL substring to match frame (for focus)
            main: Switch back to main frame (for focus)

        Returns:
            JSON with frame list or focus confirmation.
        """
        if kind == "list":
            return await format_call(client.frame_list(), render_frame_list_text)
        return await format_call(
            client.frame_focus(
                name=name or None,
                url=url or None,
                main=main,
            ),
            render_frame_focus_text,
        )
