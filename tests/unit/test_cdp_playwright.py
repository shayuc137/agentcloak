"""PlaywrightContext persistent CDP session cache (7b T0.2).

The reverse-engineering managers need a long-lived CDP session per tab (for
event streams), distinct from the seven short-lived ``new_cdp_session +
detach`` call sites elsewhere in ``playwright_ctx``. This file covers:

* cache hit — repeated ``_get_or_create_cdp_session`` returns the same object
  and creates exactly one session.
* event forwarding — the session's generic ``"event"`` listener routes into
  ``_dispatch_cdp_event``.
* invalidation — closing a tab detaches and forgets its session.
* the two ``_impl`` atoms (``_cdp_send_impl`` / ``_cdp_enable_domain_impl``).

Mock strategy
-------------
``page.context.new_cdp_session`` returns a fresh ``MagicMock`` session each
call whose ``.on`` records listeners so a test can fire the captured ``event``
handler by hand. We assert on call counts to prove caching.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcloak.browser.playwright_ctx import PlaywrightContext
from agentcloak.core.seq import RingBuffer, SeqCounter


def _make_session() -> MagicMock:
    """A mock CDPSession that records ``.on`` listeners by event name."""
    session = MagicMock()
    listeners: dict[str, list[Any]] = {}

    def _on(event: str, cb: Any) -> None:
        listeners.setdefault(event, []).append(cb)

    session.on = MagicMock(side_effect=_on)
    session._listeners = listeners
    session.send = AsyncMock(return_value={})
    session.detach = AsyncMock()
    return session


def _make_page(session: MagicMock) -> MagicMock:
    page = MagicMock()
    page.on = MagicMock()
    page.url = "https://example.com"
    page.title = AsyncMock(return_value="Example")
    page.context = MagicMock()
    page.context.new_cdp_session = AsyncMock(return_value=session)
    return page


def _make_ctx(page: MagicMock) -> PlaywrightContext:
    return PlaywrightContext(
        page=page,
        browser=MagicMock(),
        playwright=MagicMock(),
        seq_counter=SeqCounter(),
        ring_buffer=RingBuffer(),
    )


class TestSessionCache:
    @pytest.mark.asyncio
    async def test_first_call_creates_session(self) -> None:
        session = _make_session()
        page = _make_page(session)
        ctx = _make_ctx(page)

        got = await ctx._get_or_create_cdp_session()

        assert got is session
        page.context.new_cdp_session.assert_awaited_once()
        assert ctx._cdp_sessions[ctx._active_tab] is session

    @pytest.mark.asyncio
    async def test_second_call_hits_cache(self) -> None:
        session = _make_session()
        page = _make_page(session)
        ctx = _make_ctx(page)

        first = await ctx._get_or_create_cdp_session()
        second = await ctx._get_or_create_cdp_session()

        assert first is second
        # Only one underlying session ever created.
        page.context.new_cdp_session.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_event_listener_registered(self) -> None:
        session = _make_session()
        page = _make_page(session)
        ctx = _make_ctx(page)

        await ctx._get_or_create_cdp_session()

        # The generic catch-all "event" listener must be wired.
        assert "event" in session._listeners
        assert len(session._listeners["event"]) == 1

    @pytest.mark.asyncio
    async def test_event_forwarded_to_dispatch(self) -> None:
        session = _make_session()
        page = _make_page(session)
        ctx = _make_ctx(page)

        received: list[tuple[str, dict[str, Any]]] = []
        ctx._dispatch_cdp_event = (  # type: ignore[method-assign]
            lambda method, params: received.append((method, params))
        )

        await ctx._get_or_create_cdp_session()
        forward = session._listeners["event"][0]
        # Playwright emits {"method", "params"} on the generic "event" signal.
        forward({"method": "Debugger.paused", "params": {"reason": "other"}})

        assert received == [("Debugger.paused", {"reason": "other"})]

    @pytest.mark.asyncio
    async def test_event_forward_tolerates_missing_params(self) -> None:
        session = _make_session()
        page = _make_page(session)
        ctx = _make_ctx(page)

        received: list[tuple[str, dict[str, Any]]] = []
        ctx._dispatch_cdp_event = (  # type: ignore[method-assign]
            lambda method, params: received.append((method, params))
        )

        await ctx._get_or_create_cdp_session()
        forward = session._listeners["event"][0]
        forward({"method": "Debugger.resumed"})  # no "params" key

        assert received == [("Debugger.resumed", {})]


class TestSessionInvalidation:
    @pytest.mark.asyncio
    async def test_invalidate_detaches_and_removes(self) -> None:
        session = _make_session()
        page = _make_page(session)
        ctx = _make_ctx(page)

        await ctx._get_or_create_cdp_session()
        tab_id = ctx._active_tab
        assert tab_id in ctx._cdp_sessions

        await ctx._invalidate_cdp_session(tab_id)

        session.detach.assert_awaited_once()
        assert tab_id not in ctx._cdp_sessions

    @pytest.mark.asyncio
    async def test_invalidate_unknown_tab_is_noop(self) -> None:
        session = _make_session()
        page = _make_page(session)
        ctx = _make_ctx(page)

        # No session created yet — must not raise.
        await ctx._invalidate_cdp_session(999)
        session.detach.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalidate_swallows_detach_error(self) -> None:
        session = _make_session()
        session.detach = AsyncMock(side_effect=RuntimeError("already gone"))
        page = _make_page(session)
        ctx = _make_ctx(page)

        await ctx._get_or_create_cdp_session()
        tab_id = ctx._active_tab

        # Detach error must not propagate, and the cache entry must still go.
        await ctx._invalidate_cdp_session(tab_id)
        assert tab_id not in ctx._cdp_sessions

    @pytest.mark.asyncio
    async def test_tab_close_invalidates_session(self) -> None:
        """Closing a tab must drop its persistent CDP session."""
        session = _make_session()
        page = _make_page(session)
        ctx = _make_ctx(page)
        # Give the context a second tab so closing tab 0 doesn't auto-create.
        second_page = _make_page(_make_session())
        ctx._tabs[1] = second_page
        page.close = AsyncMock()

        await ctx._get_or_create_cdp_session()  # session for tab 0
        assert 0 in ctx._cdp_sessions

        await ctx._tab_close_impl(0)

        session.detach.assert_awaited_once()
        assert 0 not in ctx._cdp_sessions


class TestCdpImplAtoms:
    @pytest.mark.asyncio
    async def test_cdp_send_impl_uses_session(self) -> None:
        session = _make_session()
        session.send = AsyncMock(return_value={"result": 42})
        page = _make_page(session)
        ctx = _make_ctx(page)

        result = await ctx._cdp_send_impl("Debugger.enable", {"k": "v"})

        assert result == {"result": 42}
        session.send.assert_awaited_once_with("Debugger.enable", {"k": "v"})

    @pytest.mark.asyncio
    async def test_cdp_send_impl_coerces_non_dict(self) -> None:
        session = _make_session()
        session.send = AsyncMock(return_value=None)
        page = _make_page(session)
        ctx = _make_ctx(page)

        result = await ctx._cdp_send_impl("Debugger.resume", {})
        assert result == {}

    @pytest.mark.asyncio
    async def test_cdp_enable_domain_impl_sends_enable(self) -> None:
        session = _make_session()
        page = _make_page(session)
        ctx = _make_ctx(page)

        await ctx._cdp_enable_domain_impl("Network")

        session.send.assert_awaited_once_with("Network.enable")
