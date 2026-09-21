"""RemoteBridgeContext CDP transport (7b T0.3).

The bridge has no session object to cache — the Extension keeps one debugger
attachment per tab and already forwards every CDP event back through
``feed_message``. This file covers:

* ``_cdp_send_impl`` — delegates to ``_send("cdp", ...)`` with the standard
  ``{method, params}`` shape.
* ``_cdp_enable_domain_impl`` — sends the new ``enable_domain`` wire message.
* ``feed_message`` — every ``cdp_event`` is fanned out to
  ``_dispatch_cdp_event`` unconditionally, so managers receive ``Network.*``
  streaming events even when capture recording is off.

Mock strategy
-------------
``_send`` is patched (AsyncMock) so no real WebSocket round-trip happens; we
assert on its call args. ``feed_message`` is driven with raw JSON strings, the
same as ``test_bridge.py``.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcloak.browser.remote_ctx import RemoteBridgeContext


def _make_ctx() -> RemoteBridgeContext:
    ws = MagicMock()
    ws.closed = False
    return RemoteBridgeContext(bridge_ws=ws)


class TestCdpImplAtoms:
    @pytest.mark.asyncio
    async def test_cdp_send_impl_delegates(self) -> None:
        ctx = _make_ctx()
        ctx._send = AsyncMock(return_value={"result": "ok"})  # type: ignore[method-assign]

        result = await ctx._cdp_send_impl("Debugger.enable", {"foo": 1})

        assert result == {"result": "ok"}
        ctx._send.assert_awaited_once_with(
            "cdp", {"method": "Debugger.enable", "params": {"foo": 1}}
        )

    @pytest.mark.asyncio
    async def test_enable_domain_impl_sends_message(self) -> None:
        ctx = _make_ctx()
        ctx._send = AsyncMock(return_value={})  # type: ignore[method-assign]

        await ctx._cdp_enable_domain_impl("Network")

        ctx._send.assert_awaited_once_with("enable_domain", {"domain": "Network"})


class TestFeedMessageDispatch:
    def test_cdp_event_dispatched_to_handler(self) -> None:
        ctx = _make_ctx()
        seen: list[dict[str, Any]] = []
        ctx._on_cdp_event("Debugger.scriptParsed", seen.append)

        ctx.feed_message(
            json.dumps(
                {
                    "type": "cdp_event",
                    "method": "Debugger.scriptParsed",
                    "params": {"scriptId": "42"},
                }
            )
        )

        assert seen == [{"scriptId": "42"}]

    def test_network_event_dispatched_without_recording(self) -> None:
        """Streaming managers want Network.* events even when capture is off.

        The legacy capture path is gated on ``_capture_store.recording``; the
        manager dispatch must NOT be — otherwise WebSocket/SSE monitoring
        would silently receive nothing until someone started a capture.
        """
        ctx = _make_ctx()
        assert ctx._capture_store.recording is False
        frames: list[dict[str, Any]] = []
        ctx._on_cdp_event("Network.webSocketFrameReceived", frames.append)

        ctx.feed_message(
            json.dumps(
                {
                    "type": "cdp_event",
                    "method": "Network.webSocketFrameReceived",
                    "params": {"requestId": "ws1"},
                }
            )
        )

        assert frames == [{"requestId": "ws1"}]

    def test_prefix_handler_receives_event(self) -> None:
        ctx = _make_ctx()
        hits: list[str] = []
        ctx._on_cdp_event("Debugger.", lambda _p: hits.append("hit"))

        ctx.feed_message(
            json.dumps(
                {
                    "type": "cdp_event",
                    "method": "Debugger.paused",
                    "params": {},
                }
            )
        )

        assert hits == ["hit"]

    def test_dialog_event_still_handled_and_dispatched(self) -> None:
        """Existing dialog handling and manager dispatch are independent.

        A confirm dialog must still land in ``_pending_dialog`` AND reach any
        manager that registered for ``Page.javascriptDialogOpening``.
        """
        ctx = _make_ctx()
        dispatched: list[dict[str, Any]] = []
        ctx._on_cdp_event("Page.javascriptDialogOpening", dispatched.append)

        ctx.feed_message(
            json.dumps(
                {
                    "type": "cdp_event",
                    "method": "Page.javascriptDialogOpening",
                    "params": {
                        "type": "confirm",
                        "message": "Sure?",
                        "url": "https://example.com/",
                    },
                }
            )
        )

        # Legacy path: pending dialog populated.
        assert ctx._pending_dialog is not None
        assert ctx._pending_dialog.dialog_type == "confirm"
        # New path: manager also notified.
        assert len(dispatched) == 1
        assert dispatched[0]["message"] == "Sure?"

    def test_non_cdp_event_not_dispatched(self) -> None:
        ctx = _make_ctx()
        seen: list[Any] = []
        ctx._on_cdp_event("Debugger.paused", seen.append)

        # A response message (has id) must not trigger event dispatch.
        ctx.feed_message(json.dumps({"id": "abc", "ok": True, "data": {}}))

        assert seen == []


@pytest.mark.asyncio
async def test_hold_waits_until_release_before_continuing() -> None:
    import asyncio

    from agentcloak.browser.managers.route_manager import RouteRule

    ctx = _make_ctx()
    ctx._send = AsyncMock(return_value={})
    rule = RouteRule("/api", "hold")
    await ctx.route_manager.add(rule)
    ctx._send.reset_mock()
    task = asyncio.create_task(
        ctx._resume_paused_request(
            {
                "requestId": "r1",
                "resourceType": "XHR",
                "request": {"url": "https://example.com/api", "method": "GET"},
            }
        )
    )
    await asyncio.sleep(0)
    assert len(ctx.route_manager.pending()) == 1
    ctx._send.assert_not_awaited()
    assert ctx.route_manager.release(rule.identifier) == 1
    await asyncio.wait_for(task, 1)
    ctx._send.assert_awaited_once_with(
        "cdp", {"method": "Fetch.continueRequest", "params": {"requestId": "r1"}}
    )
    assert rule.hits == 1


@pytest.mark.asyncio
async def test_console_enable_failure_is_not_a_successful_empty_buffer() -> None:
    ctx = _make_ctx()
    ctx._send = AsyncMock(side_effect=RuntimeError("Runtime unavailable"))
    with pytest.raises(RuntimeError, match="Runtime unavailable"):
        await ctx.console_entries()
    assert not ctx._console_listening


@pytest.mark.asyncio
async def test_hold_event_keeps_originating_tab_and_cancels_on_close() -> None:
    import asyncio

    from agentcloak.browser.managers.route_manager import RouteRule

    ctx = _make_ctx()
    ctx._send = AsyncMock(return_value={})
    rule = RouteRule("/api", "hold")
    await ctx.route_manager.add(rule)
    event = {
        "type": "cdp_event",
        "tabId": 42,
        "method": "Fetch.requestPaused",
        "params": {"requestId": "r1", "request": {"url": "https://x/api"}},
    }
    ctx.feed_message(json.dumps(event))
    await asyncio.sleep(0)
    ctx.route_manager.release(rule.identifier)
    await asyncio.gather(*ctx._route_tasks)
    ctx._send.assert_awaited_with(
        "cdp",
        {"method": "Fetch.continueRequest", "params": {"requestId": "r1"}},
        tabId=42,
    )
    ctx.feed_message(json.dumps(event))
    await asyncio.sleep(0)
    assert len(ctx.route_manager.pending()) == 1
    ctx.feed_message(json.dumps({"type": "tab_event", "event": "removed", "tabId": 42}))
    await asyncio.gather(*ctx._route_tasks, return_exceptions=True)
    assert ctx.route_manager.pending() == []
