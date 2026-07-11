"""Reverse-engineering managers (Phase 7b).

Each manager owns one event-driven CDP capability (script injection, network
route interception, WebSocket/SSE streaming, the debugger). They are lazily
constructed by :class:`agentcloak.browser.base.BrowserContextBase` and reach
the browser only through the base's thin CDP interface
(``_cdp_send`` / ``_on_cdp_event`` / ``_cdp_enable_domain``), never by
touching a backend session directly.

T0 establishes the package and the base transport. T1 adds ScriptManager and
RouteManager (script injection + network route interception). T2 adds
StreamingMonitor (WebSocket/SSE capture). T3 adds DebuggerManager (breakpoints,
stepping, call-stack/scope inspection). T4 adds SourceMapManager (source-map
discovery, VLQ decode, position lookup), which mines the debugger's scripts.
"""

from __future__ import annotations

from agentcloak.browser.managers.debugger_manager import (
    BreakpointInfo,
    DebuggerManager,
    PausedState,
    ScriptInfo,
)
from agentcloak.browser.managers.hide_manager import HideManager
from agentcloak.browser.managers.route_manager import RouteManager, RouteRule
from agentcloak.browser.managers.script_manager import (
    PRESET_TEMPLATES,
    ScriptManager,
)
from agentcloak.browser.managers.sourcemap import (
    ParsedSourceMap,
    SourceMapManager,
    SourceMapping,
)
from agentcloak.browser.managers.streaming_monitor import (
    SseEvent,
    StreamingMonitor,
    WsConnectionInfo,
    WsFrame,
)

__all__ = [
    "PRESET_TEMPLATES",
    "BreakpointInfo",
    "DebuggerManager",
    "HideManager",
    "ParsedSourceMap",
    "PausedState",
    "RouteManager",
    "RouteRule",
    "ScriptInfo",
    "ScriptManager",
    "SourceMapManager",
    "SourceMapping",
    "SseEvent",
    "StreamingMonitor",
    "WsConnectionInfo",
    "WsFrame",
]
