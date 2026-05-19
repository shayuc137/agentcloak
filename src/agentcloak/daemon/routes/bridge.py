"""Bridge / Chrome-extension routes.

Two WebSocket endpoints accept extension connections — ``/bridge/ws`` for
legacy clients and ``/ext`` for the direct-attach path. Both delegate the
full lifecycle (handshake, token check, message pump, disconnect cleanup)
to :class:`BridgeService` which lives on ``app.state.bridge_service``.

The three HTTP routes here are user-facing UX: claim an existing tab,
finalize a session, and rotate the persistent auth token.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import PlainTextResponse, Response

from agentcloak.daemon.dependencies import (  # noqa: TC001
    BridgeServiceDep,
    RequiredRemoteCtxDep,
)
from agentcloak.daemon.models import (
    BridgeClaimRequest,
    BridgeFinalizeRequest,
    BridgeOpResponse,
    BridgeTokenResetResponse,
    OkEnvelope,
)
from agentcloak.daemon.routes._helpers import _ok
from agentcloak.daemon.text_renderers import wants_text

__all__ = ["router"]

logger = structlog.get_logger()

router = APIRouter()


# --- Bridge WebSocket endpoints (delegated to BridgeService) ---------------


@router.websocket("/bridge/ws")
async def handle_bridge_ws(websocket: WebSocket) -> None:
    """WebSocket endpoint for legacy bridge connections.

    Looks up :class:`BridgeService` on ``app.state`` (rather than via
    Depends, which doesn't apply to ``websocket`` routes) and hands the
    full lifecycle off in one call. Connection-mutex, token verification,
    message pumping, and disconnect cleanup all live in the service.
    """
    bridge = getattr(websocket.app.state, "bridge_service", None)
    if bridge is None:
        await websocket.close(code=1011, reason="bridge service not ready")
        return
    await bridge.handle_bridge_connection(websocket)


@router.websocket("/ext")
async def handle_ext_ws(websocket: WebSocket) -> None:
    """Direct WebSocket endpoint for the Chrome Extension.

    See :class:`BridgeService.handle_ext_connection` for the protocol
    details — this handler is just the transport adapter.
    """
    bridge = getattr(websocket.app.state, "bridge_service", None)
    if bridge is None:
        await websocket.close(code=1011, reason="bridge service not ready")
        return
    await bridge.handle_ext_connection(websocket)


# --- Bridge UX --------------------------------------------------------------


@router.post("/bridge/claim", response_model=OkEnvelope[BridgeOpResponse])
async def handle_bridge_claim(
    body: BridgeClaimRequest, remote_ctx: RequiredRemoteCtxDep, request: Request
) -> Response | dict[str, Any]:
    params: dict[str, Any] = {}
    if body.tab_id is not None:
        params["tabId"] = body.tab_id
    if body.url_pattern is not None:
        params["urlPattern"] = body.url_pattern

    result = await remote_ctx.send_command("claim", params)
    if wants_text(request):
        if isinstance(result, dict):
            claim: dict[str, Any] = dict(result)  # type: ignore[arg-type]
            tab_id: Any = claim.get("tab_id") or claim.get("tabId") or "?"
            url: Any = claim.get("url", "")
            return PlainTextResponse(f"claimed [{tab_id}] {url}".rstrip())
        return PlainTextResponse(str(result))
    return _ok(result, seq=0)


@router.post("/bridge/finalize", response_model=OkEnvelope[BridgeOpResponse])
async def handle_bridge_finalize(
    body: BridgeFinalizeRequest, remote_ctx: RequiredRemoteCtxDep, request: Request
) -> Response | dict[str, Any]:
    result = await remote_ctx.send_command("finalize", {"mode": body.mode})
    if wants_text(request):
        count = 0
        if isinstance(result, dict):
            fin: dict[str, Any] = dict(result)  # type: ignore[arg-type]
            count_val: Any = fin.get("count", fin.get("tabs", 0))
            count = int(count_val or 0)
        return PlainTextResponse(f"{body.mode} {count} tabs")
    return _ok(result, seq=0)


@router.post(
    "/bridge/token/reset",
    response_model=OkEnvelope[BridgeTokenResetResponse],
)
async def handle_bridge_token_reset(
    request: Request, bridge: BridgeServiceDep
) -> Response | dict[str, Any]:
    """Rotate the persistent bridge auth token and hot-update the daemon.

    Persists the new token to ``~/.agentcloak/config.toml`` *and* replaces
    the active bridge token via :meth:`BridgeService.set_token` so the
    previous value becomes invalid immediately — already-paired
    extensions are rejected on their next reconnect (close code 4001).
    CLI ``agentcloak bridge token --reset`` delegates here when a daemon
    is running so users don't need to restart just to rotate the credential.
    """
    from agentcloak.core.config import load_config, regenerate_bridge_token

    paths, cfg = load_config()
    new_token = regenerate_bridge_token(paths, cfg)
    bridge.set_token(new_token)
    logger.info("bridge_token_rotated", token_suffix=new_token[-4:])
    if wants_text(request):
        return PlainTextResponse(new_token)
    return _ok({"token": new_token, "rotated": True}, seq=0)
