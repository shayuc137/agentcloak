"""BrowserContextBase CDP event-stream plumbing (7b T0.1).

Covers the shared, backend-agnostic transport added for the reverse-
engineering managers:

* ``_on_cdp_event`` / ``_dispatch_cdp_event`` — exact-match and domain-prefix
  callback routing, plus exception isolation between callbacks.
* ``_cdp_enable_domain`` — idempotent ``<Domain>.enable`` via the
  ``_enabled_domains`` set.
* ``_cdp_send`` — the closed-browser / invalid-page guards that make the base
  the single audit funnel (design decision D-Q3).

Mock strategy
-------------
We exercise the concrete base methods through ``RemoteBridgeContext`` (the
cheapest concrete subclass to construct — it only needs a mock WebSocket) and
patch the two ``_impl`` atoms so no real transport is touched.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcloak.browser.remote_ctx import RemoteBridgeContext
from agentcloak.core.errors import BackendError, NavigationError


def _make_ctx() -> RemoteBridgeContext:
    ws = MagicMock()
    ws.closed = False
    return RemoteBridgeContext(bridge_ws=ws)


class TestEventRegistration:
    def test_exact_match_dispatch(self) -> None:
        ctx = _make_ctx()
        seen: list[dict[str, Any]] = []
        ctx._on_cdp_event("Debugger.paused", seen.append)

        ctx._dispatch_cdp_event("Debugger.paused", {"reason": "breakpoint"})

        assert seen == [{"reason": "breakpoint"}]

    def test_non_matching_event_ignored(self) -> None:
        ctx = _make_ctx()
        seen: list[dict[str, Any]] = []
        ctx._on_cdp_event("Debugger.paused", seen.append)

        ctx._dispatch_cdp_event("Debugger.resumed", {})

        assert seen == []

    def test_prefix_match_dispatch(self) -> None:
        """A key ending in '.' matches every event in that domain."""
        ctx = _make_ctx()
        seen: list[str] = []
        ctx._on_cdp_event("Network.", lambda _p: seen.append("hit"))

        ctx._dispatch_cdp_event("Network.webSocketFrameReceived", {})
        ctx._dispatch_cdp_event("Network.responseReceived", {})
        ctx._dispatch_cdp_event("Page.loadEventFired", {})

        assert seen == ["hit", "hit"]

    def test_exact_and_prefix_both_fire(self) -> None:
        ctx = _make_ctx()
        calls: list[str] = []
        ctx._on_cdp_event("Network.responseReceived", lambda _p: calls.append("exact"))
        ctx._on_cdp_event("Network.", lambda _p: calls.append("prefix"))

        ctx._dispatch_cdp_event("Network.responseReceived", {})

        assert calls == ["exact", "prefix"]

    def test_multiple_callbacks_same_event(self) -> None:
        ctx = _make_ctx()
        calls: list[str] = []
        ctx._on_cdp_event("Debugger.paused", lambda _p: calls.append("a"))
        ctx._on_cdp_event("Debugger.paused", lambda _p: calls.append("b"))

        ctx._dispatch_cdp_event("Debugger.paused", {})

        assert calls == ["a", "b"]

    def test_callback_exception_does_not_break_others(self) -> None:
        """One throwing callback must not stop delivery to the rest."""
        ctx = _make_ctx()
        delivered: list[str] = []

        def boom(_p: dict[str, Any]) -> None:
            raise RuntimeError("handler blew up")

        ctx._on_cdp_event("Debugger.paused", boom)
        ctx._on_cdp_event("Debugger.paused", lambda _p: delivered.append("ok"))

        # Must not propagate.
        ctx._dispatch_cdp_event("Debugger.paused", {})

        assert delivered == ["ok"]

    def test_dispatch_with_no_handlers_is_noop(self) -> None:
        ctx = _make_ctx()
        # Should not raise on an event nobody registered for.
        ctx._dispatch_cdp_event("Runtime.executionContextCreated", {"id": 1})


class TestEnableDomain:
    @pytest.mark.asyncio
    async def test_enable_calls_impl_once(self) -> None:
        ctx = _make_ctx()
        ctx._cdp_enable_domain_impl = AsyncMock()  # type: ignore[method-assign]

        await ctx._cdp_enable_domain("Network")

        ctx._cdp_enable_domain_impl.assert_awaited_once_with("Network")
        assert "Network" in ctx._enabled_domains

    @pytest.mark.asyncio
    async def test_enable_is_idempotent(self) -> None:
        ctx = _make_ctx()
        ctx._cdp_enable_domain_impl = AsyncMock()  # type: ignore[method-assign]

        await ctx._cdp_enable_domain("Debugger")
        await ctx._cdp_enable_domain("Debugger")
        await ctx._cdp_enable_domain("Debugger")

        assert ctx._cdp_enable_domain_impl.await_count == 1

    @pytest.mark.asyncio
    async def test_distinct_domains_each_enable(self) -> None:
        ctx = _make_ctx()
        ctx._cdp_enable_domain_impl = AsyncMock()  # type: ignore[method-assign]

        await ctx._cdp_enable_domain("Network")
        await ctx._cdp_enable_domain("Debugger")

        assert ctx._cdp_enable_domain_impl.await_count == 2
        assert {"Network", "Debugger"} <= ctx._enabled_domains

    @pytest.mark.asyncio
    async def test_enable_blocked_when_browser_closed(self) -> None:
        ctx = _make_ctx()
        ctx._cdp_enable_domain_impl = AsyncMock()  # type: ignore[method-assign]
        ctx._browser_closed = True

        with pytest.raises(BackendError) as excinfo:
            await ctx._cdp_enable_domain("Network")
        assert excinfo.value.error == "browser_closed"
        ctx._cdp_enable_domain_impl.assert_not_awaited()


class TestCdpSendGuards:
    @pytest.mark.asyncio
    async def test_send_delegates_to_impl(self) -> None:
        ctx = _make_ctx()
        ctx._cdp_send_impl = AsyncMock(return_value={"ok": 1})  # type: ignore[method-assign]

        result = await ctx._cdp_send("Debugger.enable", {"foo": "bar"})

        assert result == {"ok": 1}
        ctx._cdp_send_impl.assert_awaited_once_with("Debugger.enable", {"foo": "bar"})

    @pytest.mark.asyncio
    async def test_send_defaults_empty_params(self) -> None:
        ctx = _make_ctx()
        ctx._cdp_send_impl = AsyncMock(return_value={})  # type: ignore[method-assign]

        await ctx._cdp_send("Debugger.resume")

        ctx._cdp_send_impl.assert_awaited_once_with("Debugger.resume", {})

    @pytest.mark.asyncio
    async def test_send_blocked_when_browser_closed(self) -> None:
        ctx = _make_ctx()
        ctx._cdp_send_impl = AsyncMock()  # type: ignore[method-assign]
        ctx._browser_closed = True

        with pytest.raises(BackendError) as excinfo:
            await ctx._cdp_send("Debugger.enable")
        assert excinfo.value.error == "browser_closed"
        ctx._cdp_send_impl.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_blocked_when_page_invalid(self) -> None:
        ctx = _make_ctx()
        ctx._cdp_send_impl = AsyncMock()  # type: ignore[method-assign]
        ctx._page_valid = False

        with pytest.raises(NavigationError) as excinfo:
            await ctx._cdp_send("Debugger.enable")
        assert excinfo.value.error == "no_valid_page"
        ctx._cdp_send_impl.assert_not_awaited()
