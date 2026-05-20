"""Performance tool (7f) — runtime metrics via CDP ``Performance.getMetrics``.

Single read-only capability; the ``Performance`` domain is enabled lazily on
first read. A single ``action`` field keeps the shape consistent with the other
single-tool surfaces even though there's only one operation today.
"""

# pyright: reportUnusedFunction=false
# Tools register via @mcp.tool decorator side-effect.

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from agentcloak.core.text_renderers import render_performance_metrics_text
from agentcloak.mcp._format import format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def agentcloak_performance(
        action: Literal["metrics"] = "metrics",
    ) -> str:
        """Read runtime performance counters from the CDP Performance domain.

        Reports DOM node count, JS heap size, layout/recalc counts and task
        durations — useful for spotting heavy pages or confirming an action's
        cost. The domain is enabled lazily on first read.

        Actions:
          metrics — all current counters, one 'name = value' per line

        Returns: one 'name = value' per line.
        """
        _ = action  # single action today; kept for surface consistency
        return await format_call(
            client.performance_metrics(), render_performance_metrics_text
        )
