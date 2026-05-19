"""Tab-management routes — list, new, close, switch.

These mutate the tab inventory, so every write path also marks the resume
snapshot dirty so a daemon restart picks up the new state.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response

from agentcloak.daemon.dependencies import (  # noqa: TC001
    BrowserCtxDep,
    ResumeWriterDep,
)
from agentcloak.daemon.models import (
    OkEnvelope,
    TabCloseRequest,
    TabListResponse,
    TabNewRequest,
    TabOpResponse,
    TabSwitchRequest,
)
from agentcloak.daemon.routes._helpers import _ok, _update_resume
from agentcloak.daemon.text_renderers import (
    render_tab_list_text,
    render_tab_op_text,
    wants_text,
)

__all__ = ["router"]

router = APIRouter()


@router.get("/tabs", response_model=OkEnvelope[TabListResponse])
async def handle_tab_list(
    ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    tabs = await ctx.tab_list()
    data = [
        {"tab_id": t.tab_id, "url": t.url, "title": t.title, "active": t.active}
        for t in tabs
    ]
    envelope = {"tabs": data, "count": len(data)}
    if wants_text(request):
        return PlainTextResponse(render_tab_list_text(envelope))
    return _ok(envelope, seq=ctx.seq)


@router.post("/tab/new", response_model=OkEnvelope[TabOpResponse])
async def handle_tab_new(
    body: TabNewRequest,
    ctx: BrowserCtxDep,
    resume_writer: ResumeWriterDep,
    request: Request,
) -> Response | dict[str, Any]:
    result = await ctx.tab_new(body.url)
    # Tab CRUD changes the tab inventory — without this the persisted
    # resume snapshot (last touched by navigate/action) keeps reporting
    # the pre-mutation tab list.
    await _update_resume(
        resume_writer,
        ctx,
        action_summary={"kind": "tab_new", "url": body.url or ""},
    )
    if wants_text(request):
        return PlainTextResponse(render_tab_op_text("new", result))
    return _ok(result, seq=ctx.seq)


@router.post("/tab/close", response_model=OkEnvelope[TabOpResponse])
async def handle_tab_close(
    body: TabCloseRequest,
    ctx: BrowserCtxDep,
    resume_writer: ResumeWriterDep,
    request: Request,
) -> Response | dict[str, Any]:
    result = await ctx.tab_close(body.tab_id)
    await _update_resume(
        resume_writer,
        ctx,
        action_summary={"kind": "tab_close", "tab_id": body.tab_id},
    )
    if wants_text(request):
        # Preserve the closed id in the renderer payload — services don't
        # always echo it back.
        result.setdefault("tab_id", body.tab_id)
        return PlainTextResponse(render_tab_op_text("closed", result))
    return _ok(result, seq=ctx.seq)


@router.post("/tab/switch", response_model=OkEnvelope[TabOpResponse])
async def handle_tab_switch(
    body: TabSwitchRequest,
    ctx: BrowserCtxDep,
    resume_writer: ResumeWriterDep,
    request: Request,
) -> Response | dict[str, Any]:
    result = await ctx.tab_switch(body.tab_id)
    await _update_resume(
        resume_writer,
        ctx,
        action_summary={"kind": "tab_switch", "tab_id": body.tab_id},
    )
    if wants_text(request):
        result.setdefault("tab_id", body.tab_id)
        return PlainTextResponse(render_tab_op_text("switched to", result))
    return _ok(result, seq=ctx.seq)
