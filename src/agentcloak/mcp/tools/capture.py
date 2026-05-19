"""Capture tools — traffic recording (write) and querying (read)."""

# pyright: reportUnusedFunction=false
# Tools register via @mcp.tool decorator side-effect.

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from agentcloak.core.text_renderers import (
    render_capture_analyze_text,
    render_capture_export_text,
    render_capture_status_text,
    render_fetch_text,
)
from agentcloak.mcp._format import format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool(annotations=ToolAnnotations(destructiveHint=False, readOnlyHint=False))
    async def agentcloak_capture_control(
        action: Literal["start", "stop", "clear", "replay"],
        url: str = "",
        method: str = "GET",
    ) -> str:
        """Control network traffic recording for API analysis.

        Actions:
          start  — begin recording all network requests
          stop   — pause recording (data preserved)
          clear  — delete all captured data
          replay — replay the most recent captured request matching url+method

        Args:
            action: Recording control — start, stop, clear, or replay
            url: Request URL to replay (required for 'replay' action)
            method: HTTP method for replay (default GET)

        Returns:
            JSON with recording status and entry count, or replay response.
        """
        if action == "clear":
            # ``cleared N entries`` — shared renderer keeps CLI/MCP aligned.
            from agentcloak.core.text_renderers import render_capture_clear_text

            return await format_call(client.capture_clear(), render_capture_clear_text)
        if action == "stop":
            return await format_call(client.capture_stop(), render_capture_status_text)
        if action == "replay":
            return await format_call(
                client.capture_replay(url=url, method=method), render_fetch_text
            )
        return await format_call(client.capture_start(), render_capture_status_text)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def agentcloak_capture_query(
        action: Literal["status", "export", "analyze"] = "status",
        format: str = "har",
        domain: str = "",
    ) -> str:
        """Query captured traffic data.

        Actions:
          status  — check if recording is active and entry count
          export  — export captured data as HAR 1.2 or JSON
          analyze — detect API endpoint patterns from traffic

        Args:
            action: Query type — status, export, or analyze
            format: Export format for 'export' action — 'har' or 'json'
            domain: Filter by domain for 'analyze' action

        Returns:
            status: recording state + entry count.
            export: HAR 1.2 JSON or raw entry list.
            analyze: detected API patterns with method, path, auth, schema.
        """
        if action == "export":
            return await format_call(
                client.capture_export(fmt=format), render_capture_export_text
            )
        if action == "analyze":
            return await format_call(
                client.capture_analyze(domain=domain), render_capture_analyze_text
            )
        return await format_call(client.capture_status(), render_capture_status_text)
