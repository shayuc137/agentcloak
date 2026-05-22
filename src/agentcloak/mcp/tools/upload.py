"""Upload tool — file upload to input elements."""

# pyright: reportUnusedFunction=false
# Tools register via @mcp.tool decorator side-effect.

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.types import ToolAnnotations

from agentcloak.core.text_renderers import render_upload_text
from agentcloak.mcp._format import format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
    async def agentcloak_upload(
        files: list[str],
        index: int | None = None,
        nth: int = 0,
    ) -> str:
        """Upload file(s) to a file input element.

        Pass ``index`` (an [N] ref from agentcloak_snapshot) to target a visible
        file input. Omit it to auto-find ``input[type=file]`` elements — including
        the ``display:none`` inputs drag-drop uploaders hide — and attach to the
        ``nth`` one.

        Args:
            files: List of absolute file paths to upload
            index: Element [N] reference of the file input (omit to auto-find)
            nth: When auto-finding (no index), the nth file input to use (0-based)

        Returns:
            JSON with upload confirmation and file names.
        """
        return await format_call(
            client.upload(index=index, files=files, nth=nth), render_upload_text
        )
