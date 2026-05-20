"""Streaming-capture routes (7b T2) — WebSocket frames + SSE events.

Thin shells over :attr:`BrowserContextBase.streaming_monitor`, which watches CDP
``Network`` events for traffic the ordinary ``/network`` view can't see. The
first ``/ws/messages`` or ``/sse/messages`` call lazily turns capture on
(``ensure_listening`` enables the ``Network`` domain once), so a session that
never inspects streaming traffic never forces ``Network.enable`` on the stealth
backend's hot path. ``/ws/list`` reports the live connection inventory (cleared
on navigation, since open sockets die with the page).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from agentcloak.daemon.dependencies import BrowserCtxDep  # noqa: TC001
from agentcloak.daemon.models import (
    OkEnvelope,
    SseMessagesResponse,
    WsListResponse,
    WsMessagesResponse,
)
from agentcloak.daemon.routes._helpers import _ok

__all__ = ["router"]

router = APIRouter()


@router.get("/ws/list", response_model=OkEnvelope[WsListResponse])
async def handle_ws_list(ctx: BrowserCtxDep) -> dict[str, Any]:
    mgr = ctx.streaming_monitor
    await mgr.ensure_listening()
    conns = [c.to_dict() for c in mgr.ws_list()]
    return _ok({"connections": conns, "count": len(conns)}, seq=ctx.seq)


@router.get("/ws/messages", response_model=OkEnvelope[WsMessagesResponse])
async def handle_ws_messages(
    ctx: BrowserCtxDep,
    since: int = Query(
        0, description="Return WebSocket frames with seq greater than this value."
    ),
) -> dict[str, Any]:
    mgr = ctx.streaming_monitor
    await mgr.ensure_listening()
    frames, latest = mgr.ws_messages(since=since)
    return _ok({"frames": [f.to_dict() for f in frames], "seq": latest}, seq=ctx.seq)


@router.get("/sse/messages", response_model=OkEnvelope[SseMessagesResponse])
async def handle_sse_messages(
    ctx: BrowserCtxDep,
    since: int = Query(
        0, description="Return SSE events with seq greater than this value."
    ),
) -> dict[str, Any]:
    mgr = ctx.streaming_monitor
    await mgr.ensure_listening()
    events, latest = mgr.sse_messages(since=since)
    return _ok({"events": [e.to_dict() for e in events], "seq": latest}, seq=ctx.seq)
