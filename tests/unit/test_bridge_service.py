"""BridgeService lifecycle tests.

Step 1 of v0.3.x extracted ``BridgeService`` from the route handlers, but
the connection/disconnect/reconnect/mutex paths weren't covered by their
own tests — only the token verification stub in :file:`test_phase3b.py`
migrated. This file covers the remaining lifecycle behavior:

* Connection mutex (second connection while first alive → rejected)
* Disconnect cleanup (remote_ctx released, state slots reset)
* ``_existing_remote_alive`` heuristic against the inner WebSocket
* ``set_token`` writes through to the app state
* ``_fail_pending`` resolves outstanding futures with structured errors

Mock strategy
-------------
``BridgeService`` reads/writes ``app.state`` — we pass in a ``MagicMock``
state with the required slots (``bridge_token``, ``remote_ctx``,
``bridge_ws``, ``ext_ws``, ``context_manager``). The FastAPI
``WebSocket`` is also mocked: ``client.host``, ``headers``, ``accept()``,
``close()``, ``receive_text()``, ``send_text()`` are all AsyncMock /
MagicMock as appropriate.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcloak.daemon.services.bridge_service import (
    BridgeService,
    BridgeWSAdapter,
)


def _make_state(*, bridge_token: str | None = None) -> Any:
    """Build a mock app.state with the slots BridgeService touches."""
    state = MagicMock()
    state.bridge_token = bridge_token
    state.bridge_ws = None
    state.ext_ws = None
    state.remote_ctx = None
    # The state's context_manager is consulted to notify connect/disconnect.
    # Using a MagicMock keeps the test focused on service behavior.
    state.context_manager = MagicMock()
    return state


def _make_websocket(
    *, client_host: str = "127.0.0.1", auth_header: str | None = None
) -> Any:
    """Mock FastAPI WebSocket with the methods BridgeService consumes."""
    ws = MagicMock()
    client = MagicMock()
    client.host = client_host
    ws.client = client
    ws.headers = {"Authorization": auth_header} if auth_header else {}
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.send_text = AsyncMock()
    ws.receive_text = AsyncMock()
    return ws


# ---------------------------------------------------------------------------
# B4.1: Remote-context liveness heuristic
# ---------------------------------------------------------------------------


class TestRemoteLiveness:
    """``_existing_remote_alive`` gates whether ``/ext`` replaces a stale ctx.

    The standalone ``/bridge/ws`` mutex (reject with 4002) was removed with the
    bridge process; ``/ext`` always *replaces* a stale remote instead of
    rejecting. The liveness heuristic that drives that decision still matters.
    """

    @pytest.mark.asyncio
    async def test_existing_remote_alive_detects_dead_inner_ws(self) -> None:
        """A remote_ctx with closed inner WS shouldn't block new connections."""
        state = _make_state()
        # Dead remote_ctx — its _ws.closed is True.
        dead_ws = MagicMock()
        dead_ws.closed = True
        existing_remote = MagicMock()
        existing_remote._ws = dead_ws
        state.remote_ctx = existing_remote

        svc = BridgeService(state)
        # The helper is internal but encodes the replace-vs-keep contract.
        assert svc._existing_remote_alive() is False

    def test_existing_remote_alive_true_for_open_inner_ws(self) -> None:
        """An open inner WS counts as alive so ``/ext`` replaces it cleanly."""
        state = _make_state()
        live_ws = MagicMock()
        live_ws.closed = False
        existing_remote = MagicMock()
        existing_remote._ws = live_ws
        state.remote_ctx = existing_remote

        svc = BridgeService(state)
        assert svc._existing_remote_alive() is True


# ---------------------------------------------------------------------------
# B4.2: Token rotation
# ---------------------------------------------------------------------------


class TestTokenRotation:
    """Token rotation writes through to app state for the ``/ext`` hello check."""

    def test_set_token_writes_to_state(self) -> None:
        """Token rotation flips the app-state slot."""
        state = _make_state(bridge_token="old")
        svc = BridgeService(state)

        svc.set_token("new-token-xyz")
        assert state.bridge_token == "new-token-xyz"


# ---------------------------------------------------------------------------
# B4.3: Disconnect cleanup
# ---------------------------------------------------------------------------


class TestDisconnectCleanup:
    """``_fail_pending`` and state cleanup on disconnect."""

    def test_fail_pending_resolves_outstanding_futures(self) -> None:
        """Disconnect must wake any waiters with an extension_disconnected error."""
        from agentcloak.core.errors import BackendError

        state = _make_state()
        svc = BridgeService(state)

        # Build a remote_ctx stand-in with two pending futures.
        loop = asyncio.new_event_loop()
        try:
            fut1: asyncio.Future[Any] = loop.create_future()
            fut2: asyncio.Future[Any] = loop.create_future()
            remote_ctx = MagicMock()
            remote_ctx._pending = {"id1": fut1, "id2": fut2}

            svc._fail_pending(remote_ctx, "test disconnect")

            assert fut1.done()
            assert fut2.done()
            # Both futures should carry a BackendError with the
            # ``extension_disconnected`` code so callers see a structured error.
            for fut in (fut1, fut2):
                exc = fut.exception()
                assert isinstance(exc, BackendError)
                assert exc.error == "extension_disconnected"
            # The pending dict is cleared so reconnect starts fresh.
            assert remote_ctx._pending == {}
        finally:
            loop.close()

    def test_cleanup_dead_remote_resets_state_slots(self) -> None:
        """``_cleanup_dead_remote`` clears bridge_ws / ext_ws / remote_ctx."""
        state = _make_state()
        state.bridge_ws = MagicMock()
        state.ext_ws = MagicMock()
        state.remote_ctx = MagicMock()

        svc = BridgeService(state)
        svc._cleanup_dead_remote()

        # context_manager was set on the state so the service should call
        # ``on_extension_disconnected`` rather than mutate remote_ctx directly.
        state.context_manager.on_extension_disconnected.assert_called_once()
        assert state.bridge_ws is None
        assert state.ext_ws is None


# ---------------------------------------------------------------------------
# B4.4: BridgeWSAdapter
# ---------------------------------------------------------------------------


class TestBridgeWSAdapter:
    """The thin shim wrapping FastAPI WebSocket under the browser-layer Protocol."""

    @pytest.mark.asyncio
    async def test_adapter_forwards_send_str_to_send_text(self) -> None:
        ws = _make_websocket()
        adapter = BridgeWSAdapter(ws)

        await adapter.send_str("hello")
        ws.send_text.assert_awaited_once_with("hello")

    @pytest.mark.asyncio
    async def test_adapter_close_is_idempotent(self) -> None:
        """Calling close twice should not double-close the underlying WS."""
        ws = _make_websocket()
        adapter = BridgeWSAdapter(ws)

        assert adapter.closed is False
        await adapter.close()
        assert adapter.closed is True
        await adapter.close()  # second call should be a no-op
        # ws.close should still have been awaited only once.
        assert ws.close.await_count == 1

    def test_mark_closed_flips_flag_without_touching_ws(self) -> None:
        """``mark_closed`` is the lifecycle marker used in ``finally`` blocks."""
        ws = _make_websocket()
        adapter = BridgeWSAdapter(ws)

        adapter.mark_closed()
        assert adapter.closed is True
        ws.close.assert_not_called()
