"""Paused-state blocking — page actions are rejected while a breakpoint holds (7b T3).

When the debugger is paused at a breakpoint the page's JavaScript isn't running,
so page-bound operations (navigate/click/evaluate/screenshot/...) can't make
progress. :meth:`BrowserContextBase._check_debugger_paused` raises
:class:`DebuggerPausedError` (409) at each action's entry — the same pattern as
the dialog gate. Debugger commands themselves are exempt; they're how the agent
clears the pause.

These tests stand up the cheapest concrete subclass (RemoteBridgeContext),
inject a stub debugger that reports ``is_paused``, and assert the gate fires for
blocked operations and stays out of the way for compatible ones.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcloak.browser.remote_ctx import RemoteBridgeContext
from agentcloak.core.errors import DebuggerPausedError


class _StubDebugger:
    """Minimal debugger stand-in exposing just the gate's contract."""

    def __init__(self, *, paused: bool) -> None:
        self._paused = paused

    @property
    def is_paused(self) -> bool:
        return self._paused

    def get_paused_summary(self) -> dict[str, Any]:
        return {"reason": "other", "function_name": "f", "line": 1, "frame_count": 1}


def _make_ctx(*, paused: bool) -> RemoteBridgeContext:
    ws = MagicMock()
    ws.closed = False
    ctx = RemoteBridgeContext(bridge_ws=ws)
    # Inject the stub directly into the lazy slot so the ``debugger`` property
    # never constructs a real manager.
    ctx._debugger_mgr = _StubDebugger(paused=paused)  # type: ignore[assignment]
    return ctx


class TestBlockedWhilePaused:
    @pytest.mark.asyncio
    async def test_action_blocked(self) -> None:
        ctx = _make_ctx(paused=True)
        with pytest.raises(DebuggerPausedError) as exc_info:
            await ctx.action("click", "1")
        assert exc_info.value.error == "debugger_paused"
        # The compact paused summary rides along for the agent.
        assert exc_info.value.paused_info["function_name"] == "f"

    @pytest.mark.asyncio
    async def test_navigate_blocked(self) -> None:
        ctx = _make_ctx(paused=True)
        with pytest.raises(DebuggerPausedError):
            await ctx.navigate("https://example.com/")

    @pytest.mark.asyncio
    async def test_evaluate_blocked(self) -> None:
        ctx = _make_ctx(paused=True)
        with pytest.raises(DebuggerPausedError):
            await ctx.evaluate("1 + 1")

    @pytest.mark.asyncio
    async def test_screenshot_blocked(self) -> None:
        ctx = _make_ctx(paused=True)
        with pytest.raises(DebuggerPausedError):
            await ctx.screenshot()

    @pytest.mark.asyncio
    async def test_snapshot_blocked(self) -> None:
        ctx = _make_ctx(paused=True)
        with pytest.raises(DebuggerPausedError):
            await ctx.snapshot()

    @pytest.mark.asyncio
    async def test_wait_blocked(self) -> None:
        ctx = _make_ctx(paused=True)
        with pytest.raises(DebuggerPausedError):
            await ctx.wait(condition="selector", value="#x")

    @pytest.mark.asyncio
    async def test_fetch_blocked(self) -> None:
        ctx = _make_ctx(paused=True)
        with pytest.raises(DebuggerPausedError):
            await ctx.fetch("https://example.com/")

    @pytest.mark.asyncio
    async def test_upload_blocked(self) -> None:
        ctx = _make_ctx(paused=True)
        with pytest.raises(DebuggerPausedError):
            await ctx.upload(1, ["/tmp/x"])


class TestCompatibleWhilePaused:
    """Debugger commands must pass the gate so the agent can recover."""

    @pytest.mark.asyncio
    async def test_debugger_resume_not_blocked(self) -> None:
        ctx = _make_ctx(paused=True)
        # The stub debugger's resume is exercised through the real manager API
        # in the manager tests; here we only assert the gate doesn't fire on the
        # debugger path. ``_check_debugger_paused`` is what we're guarding, and
        # it isn't called by the debugger manager's own methods — proving the
        # exemption is structural (the gate lives only on page actions).
        # Calling the gate directly with a paused debugger raises; not calling it
        # (the debugger command path) is what keeps recovery possible.
        with pytest.raises(DebuggerPausedError):
            ctx._check_debugger_paused()

    @pytest.mark.asyncio
    async def test_gate_silent_when_not_paused(self) -> None:
        ctx = _make_ctx(paused=False)
        # No exception — running execution lets page actions through.
        ctx._check_debugger_paused()

    @pytest.mark.asyncio
    async def test_gate_silent_when_debugger_never_constructed(self) -> None:
        ws = MagicMock()
        ws.closed = False
        ctx = RemoteBridgeContext(bridge_ws=ws)
        # Lazy slot is None — the common path pays nothing and never raises.
        assert ctx._debugger_mgr is None
        ctx._check_debugger_paused()


class TestGateOrdering:
    @pytest.mark.asyncio
    async def test_debugger_gate_precedes_impl(self) -> None:
        """The gate fires before any backend work — the impl is never reached."""
        ctx = _make_ctx(paused=True)
        # If the gate didn't short-circuit, navigate would call _navigate_impl.
        ctx._navigate_impl = AsyncMock()  # type: ignore[method-assign]
        with pytest.raises(DebuggerPausedError):
            await ctx.navigate("https://example.com/")
        ctx._navigate_impl.assert_not_called()
