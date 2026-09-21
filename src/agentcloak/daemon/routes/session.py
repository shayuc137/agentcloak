"""Session management routes — list / close named sessions."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from agentcloak.core.errors import AgentBrowserError
from agentcloak.core.types import StealthTier
from agentcloak.daemon.dependencies import (
    ContextManagerDep,
    session_id_of,
    workspace_scope,
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
    request: Request,
    mgr: Annotated[Any, Depends(_get_session_manager)],
    all_workspaces: Annotated[
        bool, Query(description="Include every workspace.")
    ] = False,
) -> dict[str, Any]:
    sessions: list[dict[str, Any]] = (
        mgr.list_sessions(
            **workspace_scope(request),
            **({"all_workspaces": True} if all_workspaces else {}),
        )
        if mgr is not None
        else []
    )
    return _ok({"sessions": sessions}, seq=0)


@router.post("/session/close", response_model=OkEnvelope[SessionCloseResponse])
async def handle_session_close(
    body: SessionCloseRequest,
    mgr: Annotated[Any, Depends(_get_session_manager)],
    request: Request,
    ctx_mgr: ContextManagerDep,
) -> dict[str, Any]:
    caller = session_id_of(request)
    session_id = body.session_id or caller
    state = request.app.state
    if (
        body.force
        and getattr(state, "active_tier", None) == StealthTier.REMOTE_BRIDGE
        and session_id == (getattr(state, "remote_session_id", None) or "default")
        and workspace_scope(request).get("workspace_id", "")
        == getattr(state, "remote_workspace_id", "")
    ):
        raise AgentBrowserError(
            error="force_recovery_unavailable",
            hint="Force recovery is available on local browser backends only",
            action="release held requests or reconnect the Bridge before closing",
        )
    if body.force:
        closed = (
            await mgr.force_close_session(session_id, **workspace_scope(request))
            if mgr is not None
            else False
        )
    else:
        closed = (
            await ctx_mgr.close_remote_session(caller, **workspace_scope(request))
            if session_id == caller
            else False
        )
        if not closed and mgr is not None:
            closed = await mgr.close_session(session_id, **workspace_scope(request))
    return _ok({"closed": closed, "session_id": session_id}, seq=0)
