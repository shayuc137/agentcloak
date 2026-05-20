"""Route tool (7b T1.3) — intercept network requests (abort/fulfill/continue)."""

# pyright: reportUnusedFunction=false
# Tools register via @mcp.tool decorator side-effect.

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from mcp.types import ToolAnnotations

from agentcloak.core.text_renderers import (
    render_route_list_text,
    render_route_op_text,
)
from agentcloak.mcp._format import format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
    async def agentcloak_route(
        action: Literal["add", "remove", "list"] = "list",
        pattern: str = "",
        rule_action: Literal["abort", "fulfill", "continue"] = "continue",
        resource_type: str = "",
        method: str = "",
        status: int = 0,
        content_type: str = "",
        body: str = "",
    ) -> str:
        """Intercept network requests by URL pattern (abort/fulfill/continue).

        Register rules that match in-flight requests and decide their fate:
        abort them, fulfill with a synthetic response, or let them continue.
        Rules persist across navigations and replay onto new tabs. Useful for
        blocking trackers, stubbing an API, or forcing an error path.

        Actions:
          add    — register a rule (needs 'pattern' and 'rule_action')
          remove — drop a rule by 'pattern' (omit pattern to clear all)
          list   — show active rules

        Args:
            action: 'add', 'remove', or 'list'
            pattern: URL glob ('*' = any chars; no '*' = substring match)
            rule_action: Disposition for 'add' — abort, fulfill, or continue
            resource_type: Only match this resource type (xhr, image, ...)
            method: Only match this HTTP method (GET, POST, ...)
            status: Response status for a 'fulfill' rule (default 200)
            content_type: Content-Type for a 'fulfill' response
            body: Response body for a 'fulfill' response

        Returns:
            add/remove: a short state summary (active rule count).
            list: one '<action> <pattern> [filters]' per line.
        """
        if action == "add":
            return await format_call(
                client.route_add(
                    pattern=pattern,
                    action=rule_action,
                    resource_type=resource_type,
                    method=method,
                    status=status,
                    content_type=content_type,
                    body=body,
                ),
                render_route_op_text,
            )
        if action == "remove":
            return await format_call(
                client.route_remove(pattern=pattern), render_route_op_text
            )
        return await format_call(client.route_list(), render_route_list_text)
