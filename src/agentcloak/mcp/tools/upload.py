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
        index: int,
        files: list[str],
    ) -> str:
        """Upload file(s) to a file input element.

        Use agentcloak_snapshot to find the file input element's [N] ref.

        Args:
            index: Element [N] reference of the file input
            files: List of absolute file paths to upload

        Returns:
            JSON with upload confirmation and file names.
        """
        return await format_call(
            client.upload(index=index, files=files), render_upload_text
        )
