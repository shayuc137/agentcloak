"""Reverse-engineering managers (Phase 7b).

Each manager owns one event-driven CDP capability (script injection, network
route interception, WebSocket/SSE streaming, the debugger). They are lazily
constructed by :class:`agentcloak.browser.base.BrowserContextBase` and reach
the browser only through the base's thin CDP interface
(``_cdp_send`` / ``_on_cdp_event`` / ``_cdp_enable_domain``), never by
touching a backend session directly.

T0 establishes the package and the base transport. T1 adds ScriptManager and
RouteManager (script injection + network route interception); the streaming and
debugger managers land in T2-T4.
"""

from __future__ import annotations

from agentcloak.browser.managers.route_manager import RouteManager, RouteRule
from agentcloak.browser.managers.script_manager import (
    PRESET_TEMPLATES,
    ScriptManager,
)

__all__ = [
    "PRESET_TEMPLATES",
    "RouteManager",
    "RouteRule",
    "ScriptManager",
]
