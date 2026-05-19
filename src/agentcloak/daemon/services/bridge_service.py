"""BridgeService — owns Chrome Extension WebSocket lifecycle.

The daemon exposes two WebSocket endpoints for Chrome Extension connectivity:

* ``/bridge/ws`` — used by the legacy Python ``bridge`` process running on the
  user's desktop. Authenticates via ``Authorization: Bearer <token>`` header.
* ``/ext`` — direct WebSocket from the Chrome Extension. The browser
  WebSocket API cannot set custom headers, so auth is done via a ``hello``
  message containing the token.

Both endpoints share the same lifecycle: verify auth, enforce remote-context
mutex (or replace, for ``/ext``), accept the connection, instantiate
:class:`RemoteBridgeContext`, pump messages, and clean up on disconnect.
Routes used to inline ~110 lines of this logic; centralising it here keeps
the WebSocket handlers thin and makes the lifecycle one unit of testable code.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any

import orjson
import structlog
from fastapi import WebSocket, WebSocketDisconnect

from agentcloak.core.errors import BackendError

if TYPE_CHECKING:
    from agentcloak.browser.remote_ctx import RemoteBridgeContext

__all__ = ["BridgeService", "BridgeWSAdapter"]

logger = structlog.get_logger()


class BridgeWSAdapter:
    """Adapter exposing FastAPI's :class:`WebSocket` under the narrow interface
    consumed by :class:`agentcloak.browser.remote_ctx.RemoteBridgeContext`.

    The browser layer only needs ``closed`` / ``send_str`` / ``close`` /
    ``receive_text`` to operate, so we expose just those. Keeping this shim
    isolates the browser code from any specific HTTP/WS framework — if the
    daemon transport ever changes we only have to provide a new adapter, not
    rewrite the remote backend. The contract is documented as the
    ``_BridgeWS`` Protocol in ``browser/remote_ctx.py``.
    """

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    async def send_str(self, data: str) -> None:
        await self._ws.send_text(data)

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._ws.close()

    async def receive_text(self) -> str:
        return await self._ws.receive_text()

    def mark_closed(self) -> None:
        self._closed = True


class BridgeService:
    """Manages the bridge/extension WebSocket lifecycle.

    The service reads/writes the daemon's ``app.state`` slots —
    ``bridge_token``, ``bridge_ws``, ``ext_ws``, ``remote_ctx`` — so it
    doesn't need parallel state. It delegates extension connect/disconnect
    to :class:`ContextManager` so tier hot-switches observe the new remote.
    """

    def __init__(self, app_state: Any) -> None:
        self._state = app_state

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _check_bridge_token(self, websocket: WebSocket) -> bool:
        """Verify bridge auth token. Localhost connections skip auth."""
        client = websocket.client
        if client and client.host in ("127.0.0.1", "::1", "localhost"):
            return True

        expected = getattr(self._state, "bridge_token", None)
        if not expected:
            return True

        auth = websocket.headers.get("Authorization", "")
        return secrets.compare_digest(auth, f"Bearer {expected}")

    # ------------------------------------------------------------------
    # State manipulation helpers
    # ------------------------------------------------------------------

    def _existing_remote_alive(self) -> bool:
        """Return True if a remote_ctx is set and its underlying WS is open."""
        existing = getattr(self._state, "remote_ctx", None)
        if existing is None:
            return False
        ws = getattr(existing, "_ws", None)
        if ws is None:
            return False
        # ``BridgeWSAdapter`` exposes ``closed``; treat unknown shape as alive.
        closed = getattr(ws, "closed", False)
        return not bool(closed)

    def _cleanup_dead_remote(self) -> None:
        """Drop stale remote_ctx and adapter handles before accepting a new one."""
        manager = getattr(self._state, "context_manager", None)
        if manager is not None:
            manager.on_extension_disconnected()
        else:
            self._state.remote_ctx = None
        self._state.bridge_ws = None
        self._state.ext_ws = None

    def _notify_extension_connected(self, remote_ctx: Any) -> None:
        """Inform the context manager (or fall back to direct state mutation)."""
        manager = getattr(self._state, "context_manager", None)
        if manager is not None:
            manager.on_extension_connected(remote_ctx)
        else:
            self._state.remote_ctx = remote_ctx

    def _notify_extension_disconnected(self) -> None:
        manager = getattr(self._state, "context_manager", None)
        if manager is not None:
            manager.on_extension_disconnected()
        else:
            self._state.remote_ctx = None

    def _fail_pending(self, remote_ctx: Any, reason: str) -> None:
        """Resolve every outstanding bridge future with a structured error.

        Without this, callers (CLI/MCP) wait the full 60s ``bridge_timeout``
        after the extension drops the WebSocket. Failing futures eagerly
        surfaces the disconnect on the next response cycle.
        """
        pending = getattr(remote_ctx, "_pending", None)
        if not pending:
            return
        err = BackendError(
            error="extension_disconnected",
            hint=f"Extension WebSocket closed: {reason}",
            action="reconnect the Chrome extension, then retry the command",
        )
        for fut in list(pending.values()):
            if not fut.done():
                fut.set_exception(err)
        pending.clear()

    # ------------------------------------------------------------------
    # Token rotation
    # ------------------------------------------------------------------

    def set_token(self, token: str) -> None:
        """Replace the active bridge token (rotation path)."""
        self._state.bridge_token = token

    # ------------------------------------------------------------------
    # Connection handlers
    # ------------------------------------------------------------------

    async def handle_bridge_connection(self, websocket: WebSocket) -> None:
        """Run the full ``/bridge/ws`` lifecycle for one connection.

        The legacy Python bridge connects here with a Bearer-token header.
        Mutex semantics: only one remote_ctx may be active — reject when
        an alive one already exists.
        """
        if not self._check_bridge_token(websocket):
            await websocket.close(code=1008, reason="invalid bridge token")
            return

        if self._existing_remote_alive():
            await websocket.close(code=4002, reason="remote_ctx_in_use")
            logger.warning("bridge_ws_rejected", reason="remote_ctx_in_use")
            return

        self._cleanup_dead_remote()

        # Late import: pulling ``RemoteBridgeContext`` at module scope can
        # create import cycles if the browser package ever needs daemon
        # service types.
        from agentcloak.browser.remote_ctx import RemoteBridgeContext

        await websocket.accept()
        adapter = BridgeWSAdapter(websocket)
        cfg = getattr(self._state, "config", None)
        browser_cfg = cfg.browser if cfg is not None else None
        remote_ctx: RemoteBridgeContext = RemoteBridgeContext(  # type: ignore[arg-type]
            bridge_ws=adapter,
            browser_config=browser_cfg,
        )
        self._state.bridge_ws = adapter
        self._notify_extension_connected(remote_ctx)

        try:
            while True:
                data = await websocket.receive_text()
                remote_ctx.feed_message(data)
        except WebSocketDisconnect:
            pass
        finally:
            adapter.mark_closed()
            self._fail_pending(remote_ctx, "bridge websocket closed")
            self._state.bridge_ws = None
            self._notify_extension_disconnected()

    async def handle_ext_connection(self, websocket: WebSocket) -> None:
        """Run the full ``/ext`` lifecycle for one connection.

        Browser WebSocket API cannot set custom headers, so token auth
        happens at the message level: accept first, then verify the token
        in the hello message from the extension.
        """
        from agentcloak.browser.remote_ctx import RemoteBridgeContext

        client = websocket.client
        is_local = client is not None and client.host in (
            "127.0.0.1",
            "::1",
            "localhost",
        )

        await websocket.accept()

        try:
            first_msg = await websocket.receive_text()
        except WebSocketDisconnect:
            return

        try:
            hello = orjson.loads(first_msg)
        except Exception:
            await websocket.close(code=1008, reason="invalid hello message")
            return

        if not is_local:
            expected = getattr(self._state, "bridge_token", None)
            if expected:
                ext_token = hello.get("token") or ""
                # Constant-time comparison to avoid leaking token via timing.
                if not secrets.compare_digest(str(ext_token), str(expected)):
                    logger.warning(
                        "ext_ws_auth_failed",
                        remote=client.host if client else None,
                    )
                    await websocket.close(code=4001, reason="invalid bridge token")
                    return

        # ``/ext`` is exclusively used by the Chrome Extension. MV3 service
        # workers restart frequently — a new connection always replaces the
        # old one (no reject like ``/bridge/ws``).
        if self._existing_remote_alive():
            logger.info("ext_ws_replacing", remote=client.host if client else None)
            old_ws = getattr(self._state, "ext_ws", None)
            if old_ws and not getattr(old_ws, "closed", True):
                old_ws.mark_closed()

        self._cleanup_dead_remote()

        adapter = BridgeWSAdapter(websocket)
        cfg = getattr(self._state, "config", None)
        browser_cfg = cfg.browser if cfg is not None else None
        remote_ctx: RemoteBridgeContext = RemoteBridgeContext(  # type: ignore[arg-type]
            bridge_ws=adapter,
            browser_config=browser_cfg,
        )
        self._state.ext_ws = adapter
        self._notify_extension_connected(remote_ctx)

        logger.info("ext_ws_connected", remote=client.host if client else None)

        # Feed the hello message to remote_ctx in case it carries useful data.
        remote_ctx.feed_message(first_msg)

        try:
            while True:
                data = await websocket.receive_text()
                remote_ctx.feed_message(data)
        except WebSocketDisconnect:
            pass
        finally:
            adapter.mark_closed()
            self._fail_pending(remote_ctx, "extension websocket closed")
            self._state.ext_ws = None
            self._notify_extension_disconnected()
            logger.info("ext_ws_disconnected")
