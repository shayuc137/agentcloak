"""SourceMap tool (7b T4) — discover, parse, and reverse-map source positions.

One ``agentcloak_sourcemap`` tool branches on ``action`` to cover the whole
source-map surface (mirrors the script/route/streaming/debugger single-tool
pattern). Read-only: parsing and lookups never mutate the page or the debugger
session.
"""

# pyright: reportUnusedFunction=false
# Tools register via @mcp.tool decorator side-effect.

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from agentcloak.core.text_renderers import (
    render_sourcemap_get_text,
    render_sourcemap_list_text,
    render_sourcemap_lookup_text,
    render_sourcemap_source_content_text,
    render_sourcemap_sources_text,
)
from agentcloak.mcp._format import format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def agentcloak_sourcemap(
        action: Literal[
            "list",
            "get",
            "lookup",
            "sources",
            "source_content",
        ] = "list",
        script_id: str = "",
        line: int = 0,
        column: int = 0,
        source_path: str = "",
    ) -> str:
        """Discover and parse source maps to navigate minified/obfuscated code.

        Closes the reverse-engineering loop on top of the debugger: the debugger
        records each script's sourceMapURL, this tool downloads + parses the map
        (pure-Python VLQ decode) so you can reverse-map a compiled line:column
        back to the *original* source file + position and read the original
        source text. Maps download with the page's cookies; inline 'data:' URIs
        decode without a network round-trip.

        Typical flow:
          agentcloak_debugger(action='enable') → (re)load the page →
          sourcemap(action='list')  → copy a script_id with a map →
          sourcemap(action='lookup', script_id=..., line=N, column=M) or
          sourcemap(action='source_content', script_id=..., source_path=...)

        Actions:
          list           — scripts that declared a source map (script_id + url)
          get            — script_id → parsed-map metadata (sources, counts)
          lookup         — script_id + line [+ column] → original source:line:col
          sources        — script_id → original source file paths
          source_content — script_id + source_path → embedded original source text

        Args:
            action: 'list', 'get', 'lookup', 'sources', or 'source_content'
            script_id: CDP script id from the 'list' action
            line: zero-based generated (compiled) line, for 'lookup'
            column: zero-based generated column, for 'lookup'
            source_path: a path from 'sources', for 'source_content'

        Returns: action-specific text. lookup → 'source:line:col [name]' or
        'no mapping'; source_content → the raw original source.
        """
        if action == "get":
            return await format_call(
                client.sourcemap_get(script_id=script_id), render_sourcemap_get_text
            )
        if action == "lookup":
            return await format_call(
                client.sourcemap_lookup(script_id=script_id, line=line, column=column),
                render_sourcemap_lookup_text,
            )
        if action == "sources":
            return await format_call(
                client.sourcemap_sources(script_id=script_id),
                render_sourcemap_sources_text,
            )
        if action == "source_content":
            return await format_call(
                client.sourcemap_source_content(
                    script_id=script_id, source_path=source_path
                ),
                render_sourcemap_source_content_text,
            )
        # Default / "list".
        return await format_call(client.sourcemap_list(), render_sourcemap_list_text)
