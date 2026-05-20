"""Reverse-engineering managers (Phase 7b).

Each manager owns one event-driven CDP capability (script injection, network
route interception, WebSocket/SSE streaming, the debugger). They are lazily
constructed by :class:`agentcloak.browser.base.BrowserContextBase` and reach
the browser only through the base's thin CDP interface
(``_cdp_send`` / ``_on_cdp_event`` / ``_cdp_enable_domain``), never by
touching a backend session directly.

T0 establishes the package and the base transport. The concrete managers land
in T1-T4; this module is intentionally empty until then.
"""
