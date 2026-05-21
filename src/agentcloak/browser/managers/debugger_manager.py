"""DebuggerManager — breakpoints, stepping, call-stack/scope inspection (7b T3).

Drives the CDP ``Debugger`` (plus ``DOMDebugger`` for XHR breakpoints) domain so
an agent can set breakpoints, single-step paused execution, read the call stack
and scope variables, evaluate in a paused frame, and pull script sources. This
is the heaviest reverse-engineering capability: the others observe traffic, this
one *controls* execution.

State-machine shape (mirrors js-reverse-mcp ``DebuggerContext``):

* ``scriptParsed`` events accumulate into :attr:`_scripts` — the script
  inventory ``list-scripts`` reports and ``get-script-source`` reads from. This
  is also the data source SourceMap (T4) will mine for ``sourceMapURL``.
* ``paused`` flips :attr:`_paused`, stores the call frames + reason in
  :attr:`_paused_state`, and resolves :attr:`_paused_future` so a step command
  that's awaiting the next stop returns the new frames.
* ``resumed`` clears the paused state.

Everything is lazy: nothing touches the CDP ``Debugger`` domain until
:meth:`enable` runs, so a session that never debugs pays nothing and the stealth
backend's navigate path stays free of ``Debugger.enable`` (which anti-bot
systems can detect). ``disable`` tears the domain back down to restore that
silence.

All browser access goes through the base's thin CDP funnel
(``ctx._cdp_send`` / ``ctx._on_cdp_event`` / ``ctx._cdp_enable_domain``); the
manager never touches a backend session directly. The CDP event handlers are
deliberately *synchronous* because :meth:`BrowserContextBase._dispatch_cdp_event`
invokes callbacks as plain ``cb(params)`` — they only mutate in-memory state and
resolve a Future, which needs no awaiting (the documented pause pattern).
"""

# pyright: reportPrivateUsage=false
# DebuggerManager is an intentional extension of BrowserContextBase: it reaches
# the browser exclusively through the base's thin CDP funnel
# (``_cdp_send`` / ``_on_cdp_event`` / ``_cdp_enable_domain``), the documented
# collaboration (design decision D-Q3). Those names are "protected" to keep them
# off the public daemon surface, not to hide them from the managers the base
# itself constructs.

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import structlog

from agentcloak.core.errors import AgentBrowserError

if TYPE_CHECKING:
    from agentcloak.browser.base import BrowserContextBase

__all__ = [
    "BreakpointInfo",
    "DebuggerManager",
    "PausedState",
    "ScriptInfo",
]

logger = structlog.get_logger()

# How long a step command waits for the next ``Debugger.paused`` event before
# giving up. A step that runs into open-ended async work (or a page that simply
# doesn't pause again) must not hang the daemon forever — surface a timeout the
# agent can act on instead.
_STEP_TIMEOUT_S = 30.0

# Cap async call-stack depth so a deeply chained promise doesn't blow up the
# paused payload. 32 matches js-reverse-mcp / Chrome DevTools defaults.
_ASYNC_STACK_DEPTH = 32


@dataclass
class ScriptInfo:
    """One parsed script (from ``Debugger.scriptParsed``)."""

    script_id: str
    url: str
    source_map_url: str = ""
    start_line: int = 0
    end_line: int = 0
    hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "script_id": self.script_id,
            "url": self.url,
            "source_map_url": self.source_map_url,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "hash": self.hash,
        }


@dataclass
class BreakpointInfo:
    """One registered URL breakpoint (from ``Debugger.setBreakpointByUrl``)."""

    breakpoint_id: str
    url: str
    line: int
    condition: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "breakpoint_id": self.breakpoint_id,
            "url": self.url,
            "line": self.line,
            "condition": self.condition,
        }


@dataclass
class PausedState:
    """Execution-paused snapshot (from ``Debugger.paused``)."""

    call_frames: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    reason: str = ""
    hit_breakpoints: list[str] = field(default_factory=list[str])

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_frames": self.call_frames,
            "reason": self.reason,
            "hit_breakpoints": self.hit_breakpoints,
        }

    def summary(self) -> dict[str, Any]:
        """Compact view for the ``debugger_paused`` error envelope.

        The full call-frame payload is large (every frame carries a scope chain
        with object handles); the blocking error only needs enough for the agent
        to recognise *where* it's paused, so we surface the top frame's function
        name + location and the stop reason.
        """
        top: dict[str, Any] = self.call_frames[0] if self.call_frames else {}
        location: dict[str, Any] = cast("dict[str, Any]", top.get("location") or {})
        return {
            "reason": self.reason,
            "function_name": str(top.get("functionName", "")),
            "script_id": str(location.get("scriptId", "")),
            "line": int(location.get("lineNumber", 0) or 0),
            "frame_count": len(self.call_frames),
        }


class DebuggerManager:
    """Manage CDP ``Debugger`` breakpoints, stepping, and inspection."""

    def __init__(self, ctx: BrowserContextBase) -> None:
        self._ctx = ctx
        self._enabled: bool = False
        # scriptId -> info, populated by scriptParsed; cleared on navigation.
        self._scripts: dict[str, ScriptInfo] = {}
        # breakpointId -> info. URL breakpoints survive navigation natively, but
        # we keep the definitions so a tab switch (which re-enables the domain on
        # a fresh page) can restore them with new ids.
        self._breakpoints: dict[str, BreakpointInfo] = {}
        # XHR breakpoint URL patterns. Chrome resets these on navigation, so we
        # re-apply them in on_navigated().
        self._xhr_breakpoints: set[str] = set()
        self._paused: bool = False
        self._paused_state: PausedState | None = None
        # Resolved by the ``paused`` handler so a step command can park until
        # the next stop (the download-waiter Future pattern).
        self._paused_future: asyncio.Future[PausedState] | None = None

        # CDP event handlers are ctx-level (shared across tabs), so they're
        # registered once at construction — not in enable(). When the Debugger
        # domain is disabled, Chrome simply stops sending events and the handlers
        # sit idle. This decouples handler registration (ctx lifetime) from domain
        # activation (per-tab, toggled by enable/disable), preventing the
        # duplicate-handler bug that occurs when disable()→enable() cycles
        # re-append the same callbacks. _on_cdp_event deduplicates as a defence
        # layer, but the structural fix is here: register once, never again.
        ctx._on_cdp_event("Debugger.scriptParsed", self._on_script_parsed)
        ctx._on_cdp_event("Debugger.paused", self._on_paused)
        ctx._on_cdp_event("Debugger.resumed", self._on_resumed)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def enable(self) -> None:
        """Enable the CDP ``Debugger`` domain (once).

        Idempotent: a second call is a no-op. Event handlers were registered at
        construction (ctx-level, tab-independent), so enable() only activates
        the domain on the current tab's CDP session and sets the async stack
        depth. This separation prevents handler duplication on disable→enable
        cycles (tab switch, explicit re-enable).
        """
        if self._enabled:
            return
        self._enabled = True
        await self._ctx._cdp_enable_domain("Debugger")
        await self._ctx._cdp_send(
            "Debugger.setAsyncCallStackDepth", {"maxDepth": _ASYNC_STACK_DEPTH}
        )

    async def disable(self) -> None:
        """Disable the ``Debugger`` domain and clear paused/script state.

        Restores stealth silence. Breakpoint *definitions* are kept (so a tab
        switch can restore them), but the live script inventory and paused state
        are dropped because both are page-scoped and now stale. Best-effort: if
        the page already went away the disable call may fail, which we swallow so
        ``disable`` is always safe to call during teardown/reinit.
        """
        if not self._enabled:
            return
        self._enabled = False
        try:
            await self._ctx._cdp_send("Debugger.disable")
        except Exception:
            logger.debug("debugger_disable_failed", exc_info=True)
        self._scripts.clear()
        self._paused = False
        self._paused_state = None
        # Drop the enabled-domain marker so a later enable() re-issues
        # Debugger.enable on the (possibly new) page.
        self._ctx._enabled_domains.discard("Debugger")
        # Fail any in-flight step waiter so a caller awaiting a pause that will
        # never arrive (we just disabled) gets a clean cancellation.
        if self._paused_future and not self._paused_future.done():
            self._paused_future.cancel()
        self._paused_future = None

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def is_paused(self) -> bool:
        return self._paused

    def get_paused_summary(self) -> dict[str, Any]:
        """Compact paused snapshot for the ``debugger_paused`` error envelope."""
        if self._paused_state is None:
            return {}
        return self._paused_state.summary()

    # ------------------------------------------------------------------
    # CDP event handlers (synchronous — see module docstring)
    # ------------------------------------------------------------------

    def _on_script_parsed(self, params: dict[str, Any]) -> None:
        script_id = str(params.get("scriptId", ""))
        if not script_id:
            return
        self._scripts[script_id] = ScriptInfo(
            script_id=script_id,
            url=str(params.get("url", "")),
            source_map_url=str(params.get("sourceMapURL", "")),
            start_line=int(params.get("startLine", 0) or 0),
            end_line=int(params.get("endLine", 0) or 0),
            hash=str(params.get("hash", "")),
        )

    def _on_paused(self, params: dict[str, Any]) -> None:
        raw_frames: Any = params.get("callFrames") or []
        raw_hits: Any = params.get("hitBreakpoints") or []
        frames: list[dict[str, Any]] = [cast("dict[str, Any]", f) for f in raw_frames]
        self._paused = True
        self._paused_state = PausedState(
            call_frames=frames,
            reason=str(params.get("reason", "")),
            hit_breakpoints=[str(b) for b in raw_hits],
        )
        if self._paused_future and not self._paused_future.done():
            self._paused_future.set_result(self._paused_state)

    def _on_resumed(self, _params: dict[str, Any]) -> None:
        self._paused = False
        self._paused_state = None

    # ------------------------------------------------------------------
    # Breakpoints
    # ------------------------------------------------------------------

    async def set_breakpoint(
        self, url: str, line: int, condition: str = ""
    ) -> BreakpointInfo:
        """Set a URL breakpoint via ``Debugger.setBreakpointByUrl``.

        ``url`` is matched as a regex (``urlRegex``) so an agent can target a
        bundle by substring without knowing the exact query string. Returns the
        :class:`BreakpointInfo` (carrying the CDP-assigned id) and tracks it for
        ``list`` + tab-switch restoration.
        """
        await self.enable()
        params: dict[str, Any] = {"urlRegex": url, "lineNumber": line}
        if condition:
            params["condition"] = condition
        result = await self._ctx._cdp_send("Debugger.setBreakpointByUrl", params)
        breakpoint_id = str(result.get("breakpointId", ""))
        info = BreakpointInfo(
            breakpoint_id=breakpoint_id, url=url, line=line, condition=condition
        )
        if breakpoint_id:
            self._breakpoints[breakpoint_id] = info
        return info

    async def remove_breakpoint(self, breakpoint_id: str) -> bool:
        """Remove a URL breakpoint by id. Returns whether it was tracked."""
        await self._ctx._cdp_send(
            "Debugger.removeBreakpoint", {"breakpointId": breakpoint_id}
        )
        return self._breakpoints.pop(breakpoint_id, None) is not None

    def list_breakpoints(self) -> list[BreakpointInfo]:
        """Return tracked URL breakpoints."""
        return list(self._breakpoints.values())

    async def set_xhr_breakpoint(self, url_pattern: str) -> None:
        """Break when a XHR/fetch URL contains ``url_pattern`` (substring).

        An empty pattern breaks on *all* XHRs (CDP semantics). Tracked so a
        navigation can re-apply it (Chrome resets DOMDebugger state on navigate).
        """
        await self.enable()
        await self._ctx._cdp_send("DOMDebugger.setXHRBreakpoint", {"url": url_pattern})
        self._xhr_breakpoints.add(url_pattern)

    async def remove_xhr_breakpoint(self, url_pattern: str) -> bool:
        """Remove a XHR breakpoint by pattern. Returns whether it was tracked."""
        await self._ctx._cdp_send(
            "DOMDebugger.removeXHRBreakpoint", {"url": url_pattern}
        )
        if url_pattern in self._xhr_breakpoints:
            self._xhr_breakpoints.discard(url_pattern)
            return True
        return False

    def list_xhr_breakpoints(self) -> list[str]:
        """Return tracked XHR breakpoint URL patterns."""
        return sorted(self._xhr_breakpoints)

    # ------------------------------------------------------------------
    # Execution control
    # ------------------------------------------------------------------

    async def resume(self) -> None:
        """Resume execution (``Debugger.resume``). Clears paused state via event."""
        await self._ctx._cdp_send("Debugger.resume")

    async def _step(self, cdp_method: str) -> PausedState:
        """Issue a step command and park until the next ``paused`` event.

        Uses the Future-park pattern (mirrors ``download_wait``): arm the Future
        *before* the CDP call so the synchronous ``paused`` handler can resolve
        it the instant Chrome stops again. A 30s timeout protects against a step
        that never re-pauses (e.g. the program ran to completion).
        """
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[PausedState] = loop.create_future()
        self._paused_future = fut
        try:
            await self._ctx._cdp_send(cdp_method)
            return await asyncio.wait_for(fut, timeout=_STEP_TIMEOUT_S)
        finally:
            # Clear only if it's still ours — a concurrent step shouldn't have
            # its waiter wiped out by this one's cleanup.
            if self._paused_future is fut:
                self._paused_future = None

    async def step_over(self) -> PausedState:
        return await self._step("Debugger.stepOver")

    async def step_into(self) -> PausedState:
        return await self._step("Debugger.stepInto")

    async def step_out(self) -> PausedState:
        return await self._step("Debugger.stepOut")

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def get_paused_info(self) -> PausedState | None:
        """Return the current paused state, or ``None`` if running."""
        return self._paused_state

    async def get_scope_variables(self, object_id: str) -> list[dict[str, Any]]:
        """Resolve a scope object's own properties (``Runtime.getProperties``).

        ``object_id`` comes from a call frame's ``scopeChain[].object.objectId``
        in the paused info. Only own properties are returned (no prototype walk)
        so the output stays focused on the actual locals/closure values.
        """
        result = await self._ctx._cdp_send(
            "Runtime.getProperties", {"objectId": object_id, "ownProperties": True}
        )
        return list(result.get("result", []) or [])

    async def evaluate_on_frame(
        self, call_frame_id: str, expression: str
    ) -> dict[str, Any]:
        """Evaluate ``expression`` in a paused call frame's context.

        ``call_frame_id`` comes from ``callFrames[].callFrameId`` in the paused
        info. Returns the raw CDP result (``result`` remote object, plus
        ``exceptionDetails`` if the expression threw).
        """
        return await self._ctx._cdp_send(
            "Debugger.evaluateOnCallFrame",
            {"callFrameId": call_frame_id, "expression": expression},
        )

    # ------------------------------------------------------------------
    # Scripts
    # ------------------------------------------------------------------

    def list_scripts(self) -> list[ScriptInfo]:
        """Return parsed scripts seen since the last navigation."""
        return list(self._scripts.values())

    async def get_script_source(self, script_id: str) -> str:
        """Fetch a script's source text (``Debugger.getScriptSource``)."""
        result = await self._ctx._cdp_send(
            "Debugger.getScriptSource", {"scriptId": script_id}
        )
        return str(result.get("scriptSource", ""))

    async def search_in_content(
        self,
        script_id: str | None,
        query: str,
        *,
        url: str | None = None,
        is_regex: bool = False,
        case_sensitive: bool = False,
    ) -> list[dict[str, Any]]:
        """Search script content by script_id or URL pattern.

        When ``url`` is given, matches scripts whose URL contains the substring,
        searches each, and returns combined results grouped by script URL.
        """
        if url:
            return await self._search_by_url(
                url, query, is_regex=is_regex, case_sensitive=case_sensitive
            )
        if not script_id:
            raise AgentBrowserError(
                error="debugger_search_missing_target",
                hint="either script_id or url is required",
                action="pass script_id from 'debugger scripts' or --url pattern",
            )
        result = await self._ctx._cdp_send(
            "Debugger.searchInContent",
            {
                "scriptId": script_id,
                "query": query,
                "caseSensitive": case_sensitive,
                "isRegex": is_regex,
            },
        )
        return list(result.get("result", []) or [])

    async def _search_by_url(
        self,
        url_pattern: str,
        query: str,
        *,
        is_regex: bool = False,
        case_sensitive: bool = False,
    ) -> list[dict[str, Any]]:
        """Search across all scripts matching a URL substring."""
        matched = [
            (sid, info)
            for sid, info in self._scripts.items()
            if url_pattern in (info.url or "")
        ]
        if not matched:
            raise AgentBrowserError(
                error="debugger_no_matching_scripts",
                hint=f"no scripts match URL pattern {url_pattern!r}",
                action="run 'debugger scripts' to see available script URLs",
            )
        all_results: list[dict[str, Any]] = []
        for sid, info in matched:
            result = await self._ctx._cdp_send(
                "Debugger.searchInContent",
                {
                    "scriptId": sid,
                    "query": query,
                    "caseSensitive": case_sensitive,
                    "isRegex": is_regex,
                },
            )
            hits = list(result.get("result", []) or [])
            if hits:
                all_results.append(
                    {
                        "script_id": sid,
                        "url": info.url,
                        "matches": hits,
                    }
                )
        return all_results

    # ------------------------------------------------------------------
    # Anti-debug (basic)
    # ------------------------------------------------------------------

    async def skip_all_pauses(self, skip: bool) -> None:
        """Toggle ``Debugger.setSkipAllPauses``.

        When ``skip`` is true Chrome ignores every breakpoint and ``debugger``
        statement — the cheapest defence against an anti-debug loop that spams
        ``debugger;`` to stall a human investigator. Requires the domain to be
        enabled first.
        """
        await self.enable()
        await self._ctx._cdp_send("Debugger.setSkipAllPauses", {"skip": skip})

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    async def on_navigated(self) -> None:
        """React to a completed navigation: drop scripts, restore XHR breakpoints.

        URL breakpoints survive a navigation natively (Chrome re-binds them to
        the new document), so they're left alone. The script inventory and paused
        state are page-scoped and now stale, so both are cleared. XHR breakpoints
        live in DOMDebugger state that Chrome resets on navigation, so we re-apply
        each tracked pattern.
        """
        if not self._enabled:
            return
        self._scripts.clear()
        self._paused = False
        self._paused_state = None
        for url_pattern in list(self._xhr_breakpoints):
            await self._ctx._cdp_send(
                "DOMDebugger.setXHRBreakpoint", {"url": url_pattern}
            )

    async def on_tab_switched(self) -> None:
        """Re-init the debugger on the now-active page and restore breakpoints.

        A switched-to tab has its own ``Debugger`` domain state — the old
        breakpoint ids are meaningless there. So we save the definitions,
        disable+re-enable to attach to the new page, then re-set every URL and
        XHR breakpoint (URL breakpoints get fresh ids; we rebuild the map).
        This is the save→disable→re-enable→restore pattern from js-reverse-mcp's
        ``reinitDebugger``.
        """
        if not self._enabled:
            return
        saved_breakpoints = list(self._breakpoints.values())
        saved_xhr = set(self._xhr_breakpoints)
        await self.disable()
        # disable() cleared the tracking dicts' page-scoped parts but kept the
        # breakpoint definitions; clear them now so restore rebuilds cleanly
        # with the new ids.
        self._breakpoints.clear()
        self._xhr_breakpoints.clear()
        await self.enable()
        for bp in saved_breakpoints:
            await self.set_breakpoint(bp.url, bp.line, bp.condition)
        for url_pattern in saved_xhr:
            await self.set_xhr_breakpoint(url_pattern)
