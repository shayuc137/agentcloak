"""Session management routes — list / close named sessions."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from agentcloak.daemon.dependencies import (
    DEFAULT_SESSION_ID,
    ContextManagerDep,
)
from agentcloak.daemon.models import OkEnvelope
from agentcloak.daemon.models.session import (
    SessionCloseRequest,
    SessionCloseResponse,
    SessionListResponse,
)
from agentcloak.daemon.routes._helpers import _ok

__all__ = ["router"]

router = APIRouter()


def _get_session_manager(request: Request) -> Any:
    return getattr(request.app.state, "session_manager", None)


@router.get("/session/list", response_model=OkEnvelope[SessionListResponse])
async def handle_session_list(
    mgr: Annotated[Any, Depends(_get_session_manager)],
) -> dict[str, Any]:
    sessions: list[dict[str, Any]] = mgr.list_sessions() if mgr is not None else []
    return _ok({"sessions": sessions}, seq=0)


@router.post("/session/close", response_model=OkEnvelope[SessionCloseResponse])
async def handle_session_close(
    body: SessionCloseRequest,
    mgr: Annotated[Any, Depends(_get_session_manager)],
    ctx_mgr: ContextManagerDep,
) -> dict[str, Any]:
    if not body.session_id:
        closed = False
        if ctx_mgr is not None:
            await ctx_mgr.shutdown()
            closed = True
        return _ok(
            {"closed": closed, "session_id": DEFAULT_SESSION_ID},
            seq=0,
        )
    closed = False
    if mgr is not None:
        closed = await mgr.close_session(body.session_id)
    return _ok(
        {"closed": closed, "session_id": body.session_id},
        seq=0,
    )
