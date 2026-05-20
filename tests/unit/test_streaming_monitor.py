"""StreamingMonitor — WebSocket frame + SSE event capture (7b T2).

The monitor reaches the backend through two base atoms: ``_on_cdp_event``
(synchronous registration) and ``_cdp_enable_domain`` (async, idempotent). CDP
events are delivered by the base as plain ``cb(params)`` calls, so the test
harness captures the registered handlers into a dispatch table and feeds them
synthetic CDP payloads — no real backend needed. That keeps the tests focused on
the monitor's bookkeeping: lazy enable, the WS connection state machine, the
ring-buffer seq/since paging, and the navigation reset.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest

from agentcloak.browser.managers.streaming_monitor import StreamingMonitor

if TYPE_CHECKING:
    from collections.abc import Callable

    from agentcloak.browser.base import BrowserContextBase


class _FakeCtx:
    """Minimal stand-in for BrowserContextBase's CDP funnel.

    Records every ``_on_cdp_event`` registration into a method→callbacks table
    (a *list* per method, exactly like the real ``_cdp_event_handlers``, so a
    manager that wrongly re-registers handlers double-counts events here too)
    and tracks ``_cdp_enable_domain`` with the same idempotency the base has, so
    a test can assert both the lazy-enable contract and the tab-switch
    discard-then-re-enable contract.
    """

    def __init__(self) -> None:
        self.handlers: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        self.enabled: list[str] = []
        self._enabled_domains: set[str] = set()

    def _on_cdp_event(
        self, method: str, callback: Callable[[dict[str, Any]], None]
    ) -> None:
        self.handlers.setdefault(method, []).append(callback)

    async def _cdp_enable_domain(self, domain: str) -> None:
        # Mirror BrowserContextBase._cdp_enable_domain's idempotency so the
        # discard-then-re-enable tab-switch contract is observable.
        if domain in self._enabled_domains:
            return
        self.enabled.append(domain)
        self._enabled_domains.add(domain)

    def emit(self, method: str, params: dict[str, Any]) -> None:
        """Deliver a synthetic CDP event to every registered handler."""
        for cb in self.handlers.get(method, []):
            cb(params)


def _make() -> tuple[StreamingMonitor, _FakeCtx]:
    ctx = _FakeCtx()
    mgr = StreamingMonitor(cast("BrowserContextBase", ctx))
    return mgr, ctx


class TestLazyInit:
    @pytest.mark.asyncio
    async def test_ensure_listening_registers_and_enables_once(self) -> None:
        mgr, ctx = _make()

        await mgr.ensure_listening()

        # All five WS/SSE events wired, Network enabled exactly once.
        assert set(ctx.handlers) == {
            "Network.webSocketCreated",
            "Network.webSocketFrameSent",
            "Network.webSocketFrameReceived",
            "Network.webSocketClosed",
            "Network.eventSourceMessageReceived",
        }
        assert ctx.enabled == ["Network"]

    @pytest.mark.asyncio
    async def test_ensure_listening_is_idempotent(self) -> None:
        mgr, ctx = _make()

        await mgr.ensure_listening()
        await mgr.ensure_listening()
        await mgr.ensure_listening()

        # Network enabled only once across repeat calls.
        assert ctx.enabled == ["Network"]

    @pytest.mark.asyncio
    async def test_enable_domain_called_through_base(self) -> None:
        # The lazy-init contract: a fresh monitor must NOT touch the Network
        # domain until ensure_listening runs (so an unused session pays nothing).
        ctx = _FakeCtx()
        ctx_mock = AsyncMock(wraps=ctx)
        ctx_mock._on_cdp_event = ctx._on_cdp_event
        ctx_mock._cdp_enable_domain = AsyncMock(side_effect=ctx._cdp_enable_domain)
        mgr = StreamingMonitor(cast("BrowserContextBase", ctx_mock))

        ctx_mock._cdp_enable_domain.assert_not_called()
        await mgr.ensure_listening()
        ctx_mock._cdp_enable_domain.assert_awaited_once_with("Network")


class TestWebSocketStateMachine:
    @pytest.mark.asyncio
    async def test_created_frame_closed_lifecycle(self) -> None:
        mgr, ctx = _make()
        await mgr.ensure_listening()

        ctx.emit(
            "Network.webSocketCreated",
            {"requestId": "ws-1", "url": "wss://example.com/socket"},
        )
        conns = mgr.ws_list()
        assert len(conns) == 1
        assert conns[0].request_id == "ws-1"
        assert conns[0].url == "wss://example.com/socket"
        assert conns[0].status == "open"
        assert conns[0].closed_at is None

        ctx.emit("Network.webSocketClosed", {"requestId": "ws-1"})
        conn = mgr.ws_list()[0]
        assert conn.status == "closed"
        assert conn.closed_at is not None

    @pytest.mark.asyncio
    async def test_frame_sent_and_received_direction(self) -> None:
        mgr, ctx = _make()
        await mgr.ensure_listening()

        ctx.emit(
            "Network.webSocketFrameSent",
            {"requestId": "ws-1", "response": {"payloadData": "ping", "opcode": 1}},
        )
        ctx.emit(
            "Network.webSocketFrameReceived",
            {"requestId": "ws-1", "response": {"payloadData": "pong", "opcode": 1}},
        )

        frames, latest = mgr.ws_messages()
        assert latest == 2
        assert [(f.direction, f.payload) for f in frames] == [
            ("sent", "ping"),
            ("received", "pong"),
        ]

    @pytest.mark.asyncio
    async def test_closed_event_for_unknown_connection_is_ignored(self) -> None:
        mgr, ctx = _make()
        await mgr.ensure_listening()
        # No matching created event — must not raise or fabricate a connection.
        ctx.emit("Network.webSocketClosed", {"requestId": "ghost"})
        assert mgr.ws_list() == []

    @pytest.mark.asyncio
    async def test_created_without_request_id_skipped(self) -> None:
        mgr, ctx = _make()
        await mgr.ensure_listening()
        ctx.emit("Network.webSocketCreated", {"url": "wss://x"})
        assert mgr.ws_list() == []


class TestRingBufferPaging:
    @pytest.mark.asyncio
    async def test_ws_since_returns_only_newer_frames(self) -> None:
        mgr, ctx = _make()
        await mgr.ensure_listening()
        for i in range(5):
            ctx.emit(
                "Network.webSocketFrameReceived",
                {"requestId": "ws-1", "response": {"payloadData": f"f{i}"}},
            )

        frames, latest = mgr.ws_messages(since=3)

        assert latest == 5
        assert [f.seq for f in frames] == [4, 5]
        assert [f.payload for f in frames] == ["f3", "f4"]

    @pytest.mark.asyncio
    async def test_ws_seq_is_monotonic(self) -> None:
        mgr, ctx = _make()
        await mgr.ensure_listening()
        for _ in range(3):
            ctx.emit(
                "Network.webSocketFrameSent",
                {"requestId": "ws-1", "response": {"payloadData": "x"}},
            )
        frames, _ = mgr.ws_messages()
        assert [f.seq for f in frames] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_ws_since_at_latest_returns_empty(self) -> None:
        mgr, ctx = _make()
        await mgr.ensure_listening()
        ctx.emit(
            "Network.webSocketFrameSent",
            {"requestId": "ws-1", "response": {"payloadData": "x"}},
        )
        frames, latest = mgr.ws_messages(since=1)
        assert frames == []
        assert latest == 1


class TestSseCollection:
    @pytest.mark.asyncio
    async def test_sse_events_collected_with_fields(self) -> None:
        mgr, ctx = _make()
        await mgr.ensure_listening()

        ctx.emit(
            "Network.eventSourceMessageReceived",
            {
                "requestId": "sse-1",
                "eventName": "update",
                "eventId": "42",
                "data": '{"price": 100}',
            },
        )

        events, latest = mgr.sse_messages()
        assert latest == 1
        assert len(events) == 1
        ev = events[0]
        assert ev.request_id == "sse-1"
        assert ev.event_name == "update"
        assert ev.event_id == "42"
        assert ev.data == '{"price": 100}'

    @pytest.mark.asyncio
    async def test_sse_since_paging(self) -> None:
        mgr, ctx = _make()
        await mgr.ensure_listening()
        for i in range(4):
            ctx.emit(
                "Network.eventSourceMessageReceived",
                {"requestId": "sse-1", "data": f"d{i}"},
            )

        events, latest = mgr.sse_messages(since=2)
        assert latest == 4
        assert [e.seq for e in events] == [3, 4]

    @pytest.mark.asyncio
    async def test_ws_and_sse_seq_are_independent(self) -> None:
        mgr, ctx = _make()
        await mgr.ensure_listening()
        ctx.emit(
            "Network.webSocketFrameSent",
            {"requestId": "ws-1", "response": {"payloadData": "w"}},
        )
        ctx.emit(
            "Network.eventSourceMessageReceived", {"requestId": "sse-1", "data": "s"}
        )

        ws_frames, ws_latest = mgr.ws_messages()
        sse_events, sse_latest = mgr.sse_messages()
        # Each stream has its own seq counter starting at 1.
        assert ws_latest == 1
        assert sse_latest == 1
        assert ws_frames[0].seq == 1
        assert sse_events[0].seq == 1


class TestTruncation:
    @pytest.mark.asyncio
    async def test_oversized_ws_payload_is_truncated(self) -> None:
        mgr, ctx = _make()
        await mgr.ensure_listening()
        big = "a" * 5000
        ctx.emit(
            "Network.webSocketFrameReceived",
            {"requestId": "ws-1", "response": {"payloadData": big}},
        )
        frames, _ = mgr.ws_messages()
        assert frames[0].payload.endswith("…[truncated]")
        assert len(frames[0].payload) < len(big)

    @pytest.mark.asyncio
    async def test_oversized_sse_data_is_truncated(self) -> None:
        mgr, ctx = _make()
        await mgr.ensure_listening()
        big = "b" * 5000
        ctx.emit(
            "Network.eventSourceMessageReceived",
            {"requestId": "sse-1", "data": big},
        )
        events, _ = mgr.sse_messages()
        assert events[0].data.endswith("…[truncated]")


class TestNavigationReset:
    @pytest.mark.asyncio
    async def test_on_navigated_clears_connections_keeps_frames(self) -> None:
        mgr, ctx = _make()
        await mgr.ensure_listening()
        ctx.emit("Network.webSocketCreated", {"requestId": "ws-1", "url": "wss://x"})
        ctx.emit(
            "Network.webSocketFrameSent",
            {"requestId": "ws-1", "response": {"payloadData": "x"}},
        )

        await mgr.on_navigated()

        # Connections gone (their requestIds died with the page)...
        assert mgr.ws_list() == []
        # ...but frame history and the seq cursor survive for post-hoc reading.
        frames, latest = mgr.ws_messages()
        assert latest == 1
        assert len(frames) == 1

    @pytest.mark.asyncio
    async def test_seq_continues_after_navigation(self) -> None:
        mgr, ctx = _make()
        await mgr.ensure_listening()
        ctx.emit(
            "Network.webSocketFrameSent",
            {"requestId": "ws-1", "response": {"payloadData": "before"}},
        )
        await mgr.on_navigated()
        ctx.emit(
            "Network.webSocketFrameSent",
            {"requestId": "ws-2", "response": {"payloadData": "after"}},
        )
        frames, latest = mgr.ws_messages()
        # Monotonic across the navigation boundary — no reset to 1.
        assert latest == 2
        assert [f.seq for f in frames] == [1, 2]


class TestTabSwitch:
    @pytest.mark.asyncio
    async def test_re_enables_network_without_duplicate_handlers(self) -> None:
        # The regression guard: a switched-to tab has a fresh CDP session, so
        # the monitor must re-issue Network.enable — but it must NOT re-register
        # the event handlers (they live on the ctx and already fan out to every
        # session). Re-registering would double-count every frame.
        mgr, ctx = _make()
        await mgr.ensure_listening()
        assert ctx.enabled == ["Network"]

        await mgr.on_tab_switched()
        # Network re-enabled on the new session (discard cleared the marker)...
        assert ctx.enabled == ["Network", "Network"]

        # ...and one frame is recorded exactly once, not twice.
        ctx.emit(
            "Network.webSocketFrameReceived",
            {"requestId": "ws-1", "response": {"payloadData": "once"}},
        )
        frames, latest = mgr.ws_messages()
        assert latest == 1
        assert len(frames) == 1
        assert frames[0].payload == "once"

    @pytest.mark.asyncio
    async def test_clears_connections_keeps_frame_history(self) -> None:
        mgr, ctx = _make()
        await mgr.ensure_listening()
        ctx.emit("Network.webSocketCreated", {"requestId": "ws-1", "url": "wss://x"})
        ctx.emit(
            "Network.webSocketFrameSent",
            {"requestId": "ws-1", "response": {"payloadData": "x"}},
        )

        await mgr.on_tab_switched()

        # Live connections gone (the new session's requestIds differ)...
        assert mgr.ws_list() == []
        # ...but frame history + seq survive (monotonic across the switch).
        frames, latest = mgr.ws_messages()
        assert latest == 1
        assert len(frames) == 1

    @pytest.mark.asyncio
    async def test_noop_when_never_listening(self) -> None:
        # A dormant monitor (no agent ever read WS/SSE) stays dormant — a tab
        # switch must not enable Network just because a tab changed.
        mgr, ctx = _make()
        await mgr.on_tab_switched()
        assert ctx.enabled == []
