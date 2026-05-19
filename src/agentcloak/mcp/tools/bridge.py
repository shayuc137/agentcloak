"""Bridge UX tools -- tab claiming and session lifecycle."""

# pyright: reportUnusedFunction=false
# Each ``agentcloak_*`` async def below is registered with FastMCP via the
# ``@mcp.tool`` decorator's side-effect; pyright's strict mode flags them as
# unused because no other code calls them by name.

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import orjson
from mcp.types import ToolAnnotations

from agentcloak.core.text_renderers import (
    render_bridge_claim_text,
    render_bridge_finalize_text,
    render_token_text,
)
from agentcloak.mcp._format import format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def _error_envelope(error: str, hint: str, action: str) -> str:
    """Return a local validation error in the same shape as a daemon error."""
    return orjson.dumps({"error": error, "hint": hint, "action": action}).decode()


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False))
    async def agentcloak_bridge(
        action: Literal["claim", "finalize", "token_reset"] = "claim",
        tab_id: int = -1,
        url_pattern: str = "",
        mode: str = "close",
    ) -> str:
        """Manage remote browser sessions via the Chrome Extension bridge.

        Actions:
          claim       -- take control of a user-opened tab (attach debugger,
                         add to agentcloak tab group). Provide tab_id or
                         url_pattern.
          finalize    -- end the agent session. Modes:
                           close       -- close all agent-managed tabs (default)
                           handoff     -- ungroup tabs, leave open for user
                           deliverable -- rename group to 'agentcloak results'
          token_reset -- rotate the persistent bridge auth token and refresh
                         the daemon's in-memory copy. Any already-paired
                         extensions are dropped on next reconnect.

        Requires: Chrome Extension connected via bridge or daemon /ext (claim
        and finalize only; token_reset works against any running daemon).

        Args:
            action: 'claim', 'finalize', or 'token_reset'
            tab_id: Chrome tab ID to claim (only for claim action)
            url_pattern: URL substring to match for claiming (only for claim)
            mode: Finalize mode -- close, handoff, or deliverable

        Returns:
            claim:       {tabId, url, title, claimed}.
            finalize:    {mode, tabsAffected}.
            token_reset: {token, rotated}.
        """
        if action == "claim":
            if tab_id < 0 and not url_pattern:
                return _error_envelope(
                    error="missing_target",
                    hint="Provide tab_id or url_pattern to claim a tab",
                    action="set tab_id or url_pattern parameter",
                )
            return await format_call(
                client.bridge_claim(
                    tab_id=tab_id if tab_id >= 0 else None,
                    url_pattern=url_pattern or None,
                ),
                render_bridge_claim_text,
            )

        if action == "finalize":
            valid_modes = ("close", "handoff", "deliverable")
            if mode not in valid_modes:
                return _error_envelope(
                    error="invalid_mode",
                    hint=f"Mode must be one of: {', '.join(valid_modes)}",
                    action="use close, handoff, or deliverable",
                )
            # ``mode`` is request-side context — pre-bind it via a closure
            # so the renderer can emit ``close 3 tabs`` byte-identical to CLI.
            return await format_call(
                client.bridge_finalize(mode=mode),
                lambda d: render_bridge_finalize_text(d, mode=mode),
            )

        if action == "token_reset":
            return await format_call(client.bridge_token_reset(), render_token_text)

        return _error_envelope(
            error="unknown_action",
            hint=f"Unknown bridge action: {action}",
            action="use claim, finalize, or token_reset",
        )
