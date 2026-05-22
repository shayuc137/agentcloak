"""Download tool — direct-URL download, click-triggered wait, listing (7a R2)."""

# pyright: reportUnusedFunction=false
# Tools register via @mcp.tool decorator side-effect.

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from agentcloak.core.text_renderers import (
    render_download_list_text,
    render_download_text,
)
from agentcloak.mcp._format import format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool(annotations=ToolAnnotations(destructiveHint=False, readOnlyHint=False))
    async def agentcloak_download(
        action: Literal["url", "wait", "wait-click", "list"] = "url",
        url: str = "",
        output_dir: str = "",
        timeout: float = 0.0,
        index: int = 0,
        force: bool = False,
    ) -> str:
        """Download files — fetch a URL directly or capture a click-triggered download.

        Actions:
          url  — download a URL server-side using the browser's cookies. The
                 target is SSRF-checked (private/loopback hosts are rejected).
          wait — block until the next click-triggered download finishes and
                 save it (use after clicking a download button/link).
          wait-click — arm waiter, click element [index], await download. One
                 request instead of two. Use when a button triggers a download.
          list — list files downloaded during this session.

        Files are saved on the daemon host (defaulting to the system temp dir).

        Args:
            action: 'url', 'wait', 'wait-click', or 'list'
            url: URL to download (required for 'url')
            output_dir: Directory to save into (default: system temp dir)
            timeout: Seconds to wait for 'wait'/'wait-click' (default: nav timeout)
            index: Element [N] to click (required for 'wait-click')
            force: Skip pointer check on click (only for 'wait-click')

        Returns:
            url/wait/wait-click: 'saved <path> (<size> bytes)'.
            list: one '<path> | <size> bytes' per download.
        """
        if action == "list":
            return await format_call(client.download_list(), render_download_list_text)
        if action == "wait-click":
            return await format_call(
                client.download_wait_click(
                    index=index,
                    output_dir=output_dir or None,
                    timeout=timeout or None,
                    force=force,
                ),
                render_download_text,
            )
        if action == "wait":
            return await format_call(
                client.download_wait(
                    output_dir=output_dir or None,
                    timeout=timeout or None,
                ),
                render_download_text,
            )
        return await format_call(
            client.download_url(url=url, output_dir=output_dir or None),
            render_download_text,
        )
