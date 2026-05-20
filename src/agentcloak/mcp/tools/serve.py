"""Serve tool — local static file server for previewing local files (7a R7)."""

# pyright: reportUnusedFunction=false
# Tools register via @mcp.tool decorator side-effect.

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from agentcloak.core.text_renderers import (
    render_serve_status_text,
    render_serve_stop_text,
)
from agentcloak.mcp._format import error_json, format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
    async def agentcloak_serve(
        action: Literal["start", "stop", "status"] = "status",
        directory: str = "",
        port: int = 0,
    ) -> str:
        """Serve a local directory over http:// so you can navigate to local files.

        ``file://`` URLs are blocked by the security layer; this starts a
        localhost-only static server instead, then you ``agentcloak_navigate``
        to the returned URL. The server stops when the daemon exits.

        Actions:
          start  — serve 'directory' and return the base URL
          stop   — stop the running server
          status — report whether a server is running and where

        Args:
            action: 'start', 'stop', or 'status'
            directory: Directory to serve (required for 'start')
            port: Preferred port (0 = auto-allocate)

        Returns:
            start/status: 'serving <dir> at <url>' or 'file server not running'.
            stop: confirmation.
        """
        from agentcloak.core.errors import AgentBrowserError

        if action == "stop":
            return await format_call(client.serve_stop(), render_serve_stop_text)
        if action == "status":
            return await format_call(client.serve_status(), render_serve_status_text)
        if not directory:
            return error_json(
                AgentBrowserError(
                    error="missing_directory",
                    hint="directory is required to start the file server",
                    action="pass a directory parameter",
                )
            )
        return await format_call(
            client.serve_start(directory=directory, port=port or None),
            render_serve_status_text,
        )
