"""PDF tool — export the current page to a PDF file (7a R6)."""

# pyright: reportUnusedFunction=false
# Tools register via @mcp.tool decorator side-effect.

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.types import ToolAnnotations

from agentcloak.core.text_renderers import render_pdf_text
from agentcloak.mcp._format import format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
    async def agentcloak_pdf(
        output_path: str,
        format: str = "A4",
        landscape: bool = False,
        scale: float = 0.0,
        page_ranges: str = "",
    ) -> str:
        """Export the current page to a PDF file (headless Chromium only).

        The daemon renders the PDF and writes it to ``output_path`` on the
        daemon host (PDF bytes are too large to round-trip through the MCP
        transport as base64).

        Args:
            output_path: Where to save the PDF on the daemon host (required)
            format: Paper format — A4, Letter, Legal, etc.
            landscape: Landscape orientation
            scale: Render scale 0.1-2.0 (0 = browser default)
            page_ranges: Pages to print, e.g. '1-3,5' (empty = all)

        Returns:
            'saved <path> (<size> bytes)'.
        """
        return await format_call(
            client.pdf(
                format=format,
                landscape=landscape,
                scale=scale or None,
                page_ranges=page_ranges or None,
                output_path=output_path,
            ),
            render_pdf_text,
        )
