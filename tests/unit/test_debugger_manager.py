"""DebuggerManager — breakpoints, stepping, inspection state machine (7b T3).

The manager reaches the backend through three base atoms: ``_cdp_send`` (async,
returns a CDP result dict), ``_on_cdp_event`` (synchronous registration), and
``_cdp_enable_domain`` (async, idempotent). CDP events are delivered as plain
``cb(params)`` calls, so the harness captures the registered handlers and feeds
them synthetic CDP payloads — no real backend needed. ``_cdp_send`` is stubbed to
return scripted results so a test can assert both the command sequence and the
manager's bookkeeping (scripts dict, breakpoint map, paused state, the Future
park used by stepping).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

import pytest

from agentcloak.browser.managers.debugger_manager import DebuggerManager

if TYPE_CHECKING:
    from collections.abc import Callable

    from agentcloak.browser.base import BrowserContextBase


class _FakeCtx:
    """Minimal stand-in for BrowserContextBase's CDP funnel.

    Records ``_on_cdp_event`` registrations into a method→callback table, counts
    enabled domains, logs every ``_cdp_send`` call, and lets a test script the
    result returned for a given CDP method.
    """

    def __init__(self) -> None:
        self.handlers: dict[str, Callable[[dict[str, Any]], None]] = {}
        self._enabled_domains: set[str] = set()
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.results: dict[str, dict[str, Any]] = {}

    def _on_cdp_event(
        self, method: str, callback: Callable[[dict[str, Any]], None]
    ) -> None:
        self.handlers[method] = callback

    async def _cdp_enable_domain(self, domain: str) -> None:
        self._enabled_domains.add(domain)

    async def _cdp_send(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.sent.append((method, params or {}))
        return self.results.get(method, {})

    def emit(self, method: str, params: dict[str, Any]) -> None:
        """Deliver a synthetic CDP event to the registered handler."""
        self.handlers[method](params)

    def methods(self) -> list[str]:
        """All CDP method names sent so far, in order."""
        return [m for m, _ in self.sent]


def _make() -> tuple[DebuggerManager, _FakeCtx]:
    ctx = _FakeCtx()
    mgr = DebuggerManager(cast("BrowserContextBase", ctx))
    return mgr, ctx


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_enable_registers_handlers_and_enables_domain(self) -> None:
        mgr, ctx = _make()

        await mgr.enable()

        assert mgr.is_enabled is True
        assert set(ctx.handlers) == {
            "Debugger.scriptParsed",
            "Debugger.paused",
            "Debugger.resumed",
        }
        assert "Debugger" in ctx._enabled_domains
        # Async stack depth is bumped right after enabling.
        assert "Debugger.setAsyncCallStackDepth" in ctx.methods()

    @pytest.mark.asyncio
    async def test_enable_is_idempotent(self) -> None:
        mgr, ctx = _make()
        await mgr.enable()
        ctx.sent.clear()
        await mgr.enable()
        # Second enable issues no further CDP commands.
        assert ctx.sent == []

    @pytest.mark.asyncio
    async def test_disable_clears_state_and_drops_domain_marker(self) -> None:
        mgr, ctx = _make()
        await mgr.enable()
        ctx.emit("Debugger.scriptParsed", {"scriptId": "1", "url": "x.js"})
        ctx.emit("Debugger.paused", {"callFrames": [{"functionName": "f"}]})

        await mgr.disable()

        assert mgr.is_enabled is False
        assert mgr.is_paused is False
        assert mgr.list_scripts() == []
        assert "Debugger.disable" in ctx.methods()
        # The base's enabled-domain marker is dropped so a re-enable re-issues it.
        assert "Debugger" not in ctx._enabled_domains

    @pytest.mark.asyncio
    async def test_disable_swallows_cdp_failure(self) -> None:
        mgr, ctx = _make()
        await mgr.enable()

        async def _boom(
            method: str, params: dict[str, Any] | None = None
        ) -> dict[str, Any]:
            raise RuntimeError("page gone")

        ctx._cdp_send = _boom  # type: ignore[method-assign]
        # Must not raise even though Debugger.disable fails.
        await mgr.disable()
        assert mgr.is_enabled is False


class TestScriptParsed:
    @pytest.mark.asyncio
    async def test_script_parsed_populates_inventory(self) -> None:
        mgr, ctx = _make()
        await mgr.enable()

        ctx.emit(
            "Debugger.scriptParsed",
            {
                "scriptId": "42",
                "url": "https://x.com/app.js",
                "sourceMapURL": "app.js.map",
                "startLine": 0,
                "endLine": 100,
                "hash": "abc",
            },
        )

        scripts = mgr.list_scripts()
        assert len(scripts) == 1
        s = scripts[0]
        assert s.script_id == "42"
        assert s.url == "https://x.com/app.js"
        assert s.source_map_url == "app.js.map"
        assert s.end_line == 100

    @pytest.mark.asyncio
    async def test_script_parsed_without_id_skipped(self) -> None:
        mgr, ctx = _make()
        await mgr.enable()
        ctx.emit("Debugger.scriptParsed", {"url": "x.js"})
        assert mgr.list_scripts() == []


class TestPausedResumed:
    @pytest.mark.asyncio
    async def test_paused_sets_state(self) -> None:
        mgr, ctx = _make()
        await mgr.enable()

        ctx.emit(
            "Debugger.paused",
            {
                "callFrames": [
                    {
                        "functionName": "doThing",
                        "callFrameId": "cf-1",
                        "location": {"scriptId": "42", "lineNumber": 17},
                    }
                ],
                "reason": "other",
                "hitBreakpoints": ["1:0:42"],
            },
        )

        assert mgr.is_paused is True
        state = mgr.get_paused_info()
        assert state is not None
        assert state.reason == "other"
        assert state.hit_breakpoints == ["1:0:42"]
        # Summary surfaces the top frame for the debugger_paused error envelope.
        summary = mgr.get_paused_summary()
        assert summary["function_name"] == "doThing"
        assert summary["line"] == 17
        assert summary["frame_count"] == 1

    @pytest.mark.asyncio
    async def test_resumed_clears_state(self) -> None:
        mgr, ctx = _make()
        await mgr.enable()
        ctx.emit("Debugger.paused", {"callFrames": []})
        assert mgr.is_paused is True

        ctx.emit("Debugger.resumed", {})
        assert mgr.is_paused is False
        assert mgr.get_paused_info() is None


class TestBreakpoints:
    @pytest.mark.asyncio
    async def test_set_breakpoint_tracks_and_returns_id(self) -> None:
        mgr, ctx = _make()
        ctx.results["Debugger.setBreakpointByUrl"] = {"breakpointId": "bp-1"}

        info = await mgr.set_breakpoint("app.js", 10, "x > 1")

        assert info.breakpoint_id == "bp-1"
        assert info.url == "app.js"
        assert info.line == 10
        assert info.condition == "x > 1"
        assert mgr.list_breakpoints()[0].breakpoint_id == "bp-1"
        # setBreakpointByUrl gets the regex + line + condition.
        _, params = next(
            (m, p) for m, p in ctx.sent if m == "Debugger.setBreakpointByUrl"
        )
        assert params["urlRegex"] == "app.js"
        assert params["lineNumber"] == 10
        assert params["condition"] == "x > 1"

    @pytest.mark.asyncio
    async def test_set_breakpoint_auto_enables(self) -> None:
        mgr, ctx = _make()
        ctx.results["Debugger.setBreakpointByUrl"] = {"breakpointId": "bp-1"}
        # Not enabled yet — set_breakpoint should enable first.
        await mgr.set_breakpoint("app.js", 1)
        assert mgr.is_enabled is True
        assert "Debugger" in ctx._enabled_domains

    @pytest.mark.asyncio
    async def test_remove_breakpoint(self) -> None:
        mgr, ctx = _make()
        ctx.results["Debugger.setBreakpointByUrl"] = {"breakpointId": "bp-1"}
        await mgr.set_breakpoint("app.js", 1)

        removed = await mgr.remove_breakpoint("bp-1")
        assert removed is True
        assert mgr.list_breakpoints() == []
        assert ("Debugger.removeBreakpoint", {"breakpointId": "bp-1"}) in ctx.sent

    @pytest.mark.asyncio
    async def test_remove_unknown_breakpoint_returns_false(self) -> None:
        mgr, _ = _make()
        await mgr.enable()
        assert await mgr.remove_breakpoint("ghost") is False

    @pytest.mark.asyncio
    async def test_xhr_breakpoint_set_and_remove(self) -> None:
        mgr, ctx = _make()
        await mgr.set_xhr_breakpoint("/api/")
        assert mgr.list_xhr_breakpoints() == ["/api/"]
        assert ("DOMDebugger.setXHRBreakpoint", {"url": "/api/"}) in ctx.sent

        removed = await mgr.remove_xhr_breakpoint("/api/")
        assert removed is True
        assert mgr.list_xhr_breakpoints() == []
        assert ("DOMDebugger.removeXHRBreakpoint", {"url": "/api/"}) in ctx.sent


class TestStepping:
    @pytest.mark.asyncio
    async def test_step_over_parks_until_paused_event(self) -> None:
        mgr, ctx = _make()
        await mgr.enable()
        ctx.emit("Debugger.paused", {"callFrames": [{"functionName": "a"}]})

        async def _drive() -> None:
            # Let step_over arm the Future + send the CDP command, then deliver
            # the next paused event so the await resolves.
            await asyncio.sleep(0)
            ctx.emit(
                "Debugger.paused",
                {"callFrames": [{"functionName": "b"}], "reason": "other"},
            )

        driver = asyncio.create_task(_drive())
        state = await mgr.step_over()
        await driver

        assert "Debugger.stepOver" in ctx.methods()
        assert state.call_frames[0]["functionName"] == "b"

    @pytest.mark.asyncio
    async def test_step_into_and_out_use_right_cdp_methods(self) -> None:
        for step_name, cdp_method in (
            ("step_into", "Debugger.stepInto"),
            ("step_out", "Debugger.stepOut"),
        ):
            mgr, ctx = _make()
            await mgr.enable()

            # Bind ``ctx`` via a default arg so the closure captures this
            # iteration's value, not the loop variable (ruff B023).
            async def _drive(c: _FakeCtx = ctx) -> None:
                await asyncio.sleep(0)
                c.emit("Debugger.paused", {"callFrames": []})

            driver = asyncio.create_task(_drive())
            await getattr(mgr, step_name)()
            await driver
            assert cdp_method in ctx.methods()

    @pytest.mark.asyncio
    async def test_step_times_out_when_no_pause(self, monkeypatch: Any) -> None:
        import agentcloak.browser.managers.debugger_manager as dm

        monkeypatch.setattr(dm, "_STEP_TIMEOUT_S", 0.01)
        mgr, _ = _make()
        await mgr.enable()
        # No paused event is ever delivered → wait_for raises TimeoutError.
        with pytest.raises(TimeoutError):
            await mgr.step_over()


class TestInspection:
    @pytest.mark.asyncio
    async def test_get_scope_variables(self) -> None:
        mgr, ctx = _make()
        ctx.results["Runtime.getProperties"] = {
            "result": [{"name": "x", "value": {"value": 1}}]
        }
        variables = await mgr.get_scope_variables("obj-1")
        assert variables == [{"name": "x", "value": {"value": 1}}]
        _, params = next((m, p) for m, p in ctx.sent if m == "Runtime.getProperties")
        assert params["objectId"] == "obj-1"
        assert params["ownProperties"] is True

    @pytest.mark.asyncio
    async def test_evaluate_on_frame(self) -> None:
        mgr, ctx = _make()
        ctx.results["Debugger.evaluateOnCallFrame"] = {
            "result": {"value": 42, "type": "number"}
        }
        result = await mgr.evaluate_on_frame("cf-1", "6 * 7")
        assert result["result"]["value"] == 42
        _, params = next(
            (m, p) for m, p in ctx.sent if m == "Debugger.evaluateOnCallFrame"
        )
        assert params["callFrameId"] == "cf-1"
        assert params["expression"] == "6 * 7"

    @pytest.mark.asyncio
    async def test_get_script_source(self) -> None:
        mgr, ctx = _make()
        ctx.results["Debugger.getScriptSource"] = {"scriptSource": "console.log(1)"}
        source = await mgr.get_script_source("42")
        assert source == "console.log(1)"

    @pytest.mark.asyncio
    async def test_search_in_content(self) -> None:
        mgr, ctx = _make()
        ctx.results["Debugger.searchInContent"] = {
            "result": [{"lineNumber": 3, "lineContent": "secret = 'x'"}]
        }
        matches = await mgr.search_in_content("42", "secret", is_regex=True)
        assert matches[0]["lineNumber"] == 3
        _, params = next((m, p) for m, p in ctx.sent if m == "Debugger.searchInContent")
        assert params["query"] == "secret"
        assert params["isRegex"] is True


class TestAntiDebug:
    @pytest.mark.asyncio
    async def test_skip_all_pauses(self) -> None:
        mgr, ctx = _make()
        await mgr.skip_all_pauses(True)
        # Auto-enables, then issues setSkipAllPauses with the flag.
        assert mgr.is_enabled is True
        assert ("Debugger.setSkipAllPauses", {"skip": True}) in ctx.sent


class TestNavigationLifecycle:
    @pytest.mark.asyncio
    async def test_on_navigated_clears_scripts_and_restores_xhr(self) -> None:
        mgr, ctx = _make()
        await mgr.enable()
        ctx.emit("Debugger.scriptParsed", {"scriptId": "1", "url": "a.js"})
        await mgr.set_xhr_breakpoint("/api/")
        ctx.sent.clear()

        await mgr.on_navigated()

        # Scripts are page-scoped → cleared.
        assert mgr.list_scripts() == []
        # XHR breakpoint is re-applied (Chrome resets DOMDebugger on navigation).
        assert ("DOMDebugger.setXHRBreakpoint", {"url": "/api/"}) in ctx.sent
        # XHR breakpoint stays tracked.
        assert mgr.list_xhr_breakpoints() == ["/api/"]

    @pytest.mark.asyncio
    async def test_on_navigated_noop_when_disabled(self) -> None:
        mgr, ctx = _make()
        # Never enabled.
        await mgr.on_navigated()
        assert ctx.sent == []


class TestTabSwitchLifecycle:
    @pytest.mark.asyncio
    async def test_on_tab_switched_reinits_and_restores_breakpoints(self) -> None:
        mgr, ctx = _make()
        ctx.results["Debugger.setBreakpointByUrl"] = {"breakpointId": "bp-1"}
        await mgr.set_breakpoint("app.js", 10, "cond")
        await mgr.set_xhr_breakpoint("/api/")
        ctx.sent.clear()

        # New page hands out a different breakpoint id on restore.
        ctx.results["Debugger.setBreakpointByUrl"] = {"breakpointId": "bp-2"}
        await mgr.on_tab_switched()

        methods = ctx.methods()
        # disable → re-enable → re-set breakpoints (save/disable/re-enable/restore).
        assert "Debugger.disable" in methods
        assert methods.count("Debugger.setBreakpointByUrl") == 1
        # The breakpoint is restored with the NEW id; the old one is gone.
        ids = [b.breakpoint_id for b in mgr.list_breakpoints()]
        assert ids == ["bp-2"]
        # XHR breakpoint restored too.
        assert mgr.list_xhr_breakpoints() == ["/api/"]
        assert mgr.is_enabled is True

    @pytest.mark.asyncio
    async def test_on_tab_switched_noop_when_disabled(self) -> None:
        mgr, ctx = _make()
        await mgr.on_tab_switched()
        assert ctx.sent == []
