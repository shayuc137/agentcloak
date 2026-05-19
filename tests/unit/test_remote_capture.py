"""RemoteBridgeContext CDP Network capture state machine tests.

``remote_ctx._handle_network_event`` builds :class:`CaptureEntry` records
from CDP ``Network.*`` events forwarded by the Chrome Extension. The state
machine is 135 lines and had zero direct tests — the integration suite
exercises it through real CDP traffic, which is too expensive for a
regression net.

The protocol the Extension fires:

1. ``Network.requestWillBeSent`` — opens a pending entry for the request id
2. ``Network.responseReceived`` — fills in status / headers / content-type
3. ``Network.loadingFinished`` — triggers an async ``getResponseBody``
   fetch and pushes the finalised entry into the :class:`CaptureStore`

Tests construct these event sequences and assert on the entries that end
up in the store.

Mock strategy
-------------
We patch :meth:`RemoteBridgeContext._send` so the state machine's
``getResponseBody`` round-trip returns canned bodies. The capture store
on the context is real (built by ``BrowserContextBase``).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcloak.browser.remote_ctx import RemoteBridgeContext
from agentcloak.core.capture import MAX_BODY_SIZE


def _make_ctx() -> RemoteBridgeContext:
    ws = MagicMock()
    ws.closed = False
    return RemoteBridgeContext(bridge_ws=ws)


def _request_event(
    *,
    request_id: str,
    url: str,
    method: str = "GET",
    resource_type: str = "xhr",
) -> dict[str, Any]:
    """Build a CDP Network.requestWillBeSent params dict."""
    return {
        "requestId": request_id,
        "request": {
            "url": url,
            "method": method,
            "headers": {"User-Agent": "Mozilla/5.0"},
        },
        "type": resource_type,
        "wallTime": 1715000000.0,
    }


def _response_event(
    *, request_id: str, status: int = 200, mime_type: str = "application/json"
) -> dict[str, Any]:
    """Build a CDP Network.responseReceived params dict."""
    return {
        "requestId": request_id,
        "response": {
            "status": status,
            "mimeType": mime_type,
            "headers": {"Content-Type": mime_type},
        },
    }


# ---------------------------------------------------------------------------
# B5.1: Full happy-path sequence
# ---------------------------------------------------------------------------


class TestCaptureStateMachineHappyPath:
    """requestWillBeSent → responseReceived → loadingFinished → CaptureEntry."""

    @pytest.mark.asyncio
    async def test_full_sequence_produces_capture_entry(self) -> None:
        ctx = _make_ctx()
        ctx._capture_store.start()

        # _finalize_capture calls _send for getResponseBody — stub it.
        ctx._send = AsyncMock(  # type: ignore[method-assign]
            return_value={"body": '{"ok":true}', "base64Encoded": False}
        )

        # Fire the three events.
        ctx._handle_network_event(
            "Network.requestWillBeSent",
            _request_event(request_id="req-1", url="https://api.example.com/v1/data"),
        )
        ctx._handle_network_event(
            "Network.responseReceived", _response_event(request_id="req-1")
        )
        ctx._handle_network_event("Network.loadingFinished", {"requestId": "req-1"})

        # The finalize is an asyncio.ensure_future task — wait for it to finish.
        # The set is exposed as ``_capture_tasks``; gather what's there.
        tasks = list(ctx._capture_tasks)
        if tasks:
            await asyncio.gather(*tasks)

        entries = ctx._capture_store.entries()
        assert len(entries) == 1
        entry = entries[0]
        assert entry.url == "https://api.example.com/v1/data"
        assert entry.method == "GET"
        assert entry.status == 200
        assert entry.content_type == "application/json"
        # Body is fetched + truncated.
        assert entry.response_body == '{"ok":true}'

    @pytest.mark.asyncio
    async def test_request_without_response_does_not_emit(self) -> None:
        """A request that never gets responseReceived is dropped silently."""
        ctx = _make_ctx()
        ctx._capture_store.start()

        ctx._handle_network_event(
            "Network.requestWillBeSent",
            _request_event(request_id="req-2", url="https://api.example.com/x"),
        )
        # No responseReceived, no loadingFinished — request times out at the
        # Extension level. The state machine leaks an entry in _pending_captures
        # which is fine — _capture_teardown_impl will clear it on stop.
        assert "req-2" in ctx._pending_captures
        assert len(ctx._capture_store.entries()) == 0


# ---------------------------------------------------------------------------
# B5.2: Body truncation at MAX_BODY_SIZE
# ---------------------------------------------------------------------------


class TestBodyTruncation:
    """Response bodies larger than MAX_BODY_SIZE must be truncated."""

    @pytest.mark.asyncio
    async def test_oversized_body_truncated_to_max_size(self) -> None:
        ctx = _make_ctx()
        ctx._capture_store.start()

        # Build a body larger than MAX_BODY_SIZE.
        huge = "x" * (MAX_BODY_SIZE + 5000)
        ctx._send = AsyncMock(  # type: ignore[method-assign]
            return_value={"body": huge, "base64Encoded": False}
        )

        ctx._handle_network_event(
            "Network.requestWillBeSent",
            _request_event(request_id="big", url="https://api.example.com/big"),
        )
        ctx._handle_network_event(
            "Network.responseReceived", _response_event(request_id="big")
        )
        ctx._handle_network_event("Network.loadingFinished", {"requestId": "big"})

        tasks = list(ctx._capture_tasks)
        if tasks:
            await asyncio.gather(*tasks)

        entries = ctx._capture_store.entries()
        assert len(entries) == 1
        # truncate_body caps at MAX_BODY_SIZE exactly.
        assert entries[0].response_body is not None
        assert len(entries[0].response_body) == MAX_BODY_SIZE


# ---------------------------------------------------------------------------
# B5.3: loadingFailed handling
# ---------------------------------------------------------------------------


class TestLoadingFailed:
    """Network.loadingFailed should drop entries without a status."""

    @pytest.mark.asyncio
    async def test_loading_failed_before_response_drops_entry(self) -> None:
        """Request died before responseReceived → no entry recorded."""
        ctx = _make_ctx()
        ctx._capture_store.start()
        ctx._send = AsyncMock()  # type: ignore[method-assign]

        # request → loadingFailed (no responseReceived).
        ctx._handle_network_event(
            "Network.requestWillBeSent",
            _request_event(request_id="dead", url="https://api.example.com/dead"),
        )
        ctx._handle_network_event("Network.loadingFailed", {"requestId": "dead"})

        # Give any spawned tasks a chance (shouldn't be any in this path).
        tasks = list(ctx._capture_tasks)
        if tasks:
            await asyncio.gather(*tasks)

        # Status was 0 (never set), so the entry is dropped — no
        # getResponseBody attempt.
        assert len(ctx._capture_store.entries()) == 0
        # The pending-captures slot must also be cleared.
        assert "dead" not in ctx._pending_captures
