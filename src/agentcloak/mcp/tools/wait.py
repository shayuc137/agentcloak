"""Wait tool — conditional waiting."""

# pyright: reportUnusedFunction=false
# Tools register via @mcp.tool decorator side-effect.

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from agentcloak.core.text_renderers import render_wait_text
from agentcloak.mcp._format import format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def register(mcp: FastMCP, client: DaemonClient) -> None:
    cfg = client.config  # single shared client snapshot.

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def agentcloak_wait(
        condition: Literal["selector", "url", "load", "js", "ms"],
        value: str = "",
        timeout: int = cfg.action_timeout,
        state: str = "visible",
    ) -> str:
        """Wait for a condition before continuing.

        Args:
            condition: What to wait for:
                - selector: CSS selector to appear (use 'state' for visibility)
                - url: URL pattern (glob, e.g. '**/dashboard')
                - load: Page load state (load, domcontentloaded, networkidle)
                - js: JS expression that must return truthy
                - ms: Sleep for N milliseconds
            value: The selector/url/state/expression/milliseconds value
            timeout: Max wait time in milliseconds (default from
                config.action_timeout)
            state: Element state for selector condition:
                visible (default), hidden, attached, detached

        Returns:
            JSON with condition matched and elapsed_ms.
        """
        return await format_call(
            client.wait(
                condition=condition,
                value=value,
                timeout=timeout,
                state=state,
            ),
            render_wait_text,
        )
