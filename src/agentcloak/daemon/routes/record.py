"""Page-pinned recording control; artifacts returned to the requesting client."""

from typing import Any

from fastapi import APIRouter

from agentcloak.daemon.dependencies import BrowserCtxDep
from agentcloak.daemon.models import OkEnvelope
from agentcloak.daemon.models.record import RecordResponse, RecordStartRequest
from agentcloak.daemon.routes._helpers import _ok

router = APIRouter()


@router.post("/record/start", response_model=OkEnvelope[RecordResponse])
async def handle_record_start(
    body: RecordStartRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    return _ok(await ctx.record_start(**body.model_dump()), seq=ctx.seq)


@router.get("/record/status", response_model=OkEnvelope[RecordResponse])
async def handle_record_status(ctx: BrowserCtxDep) -> dict[str, Any]:
    return _ok(await ctx.record_status(), seq=ctx.seq)


@router.post("/record/stop", response_model=OkEnvelope[RecordResponse])
async def handle_record_stop(ctx: BrowserCtxDep) -> dict[str, Any]:
    return _ok(await ctx.record_stop(), seq=ctx.seq)
