"""Page environment, viewport and CDP routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from agentcloak.core.emulation import PageEmulation
from agentcloak.daemon.dependencies import BrowserCtxDep  # noqa: TC001
from agentcloak.daemon.models import (
    HeadersRequest,
    HeadersResponse,
    OkEnvelope,
)
from agentcloak.daemon.models.emulation import (
    CdpSendRequest,
    CdpSendResponse,
    EmulationRequest,
    ViewportRequest,
    ViewportResponse,
)
from agentcloak.daemon.routes._helpers import _ok

__all__ = ["router"]

router = APIRouter()


@router.post("/emulation/headers", response_model=OkEnvelope[HeadersResponse])
async def handle_emulation_headers(
    body: HeadersRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    result = await ctx.set_extra_headers(body.headers)
    return _ok(result, seq=ctx.seq)


@router.post("/viewport", response_model=OkEnvelope[ViewportResponse])
async def handle_viewport(body: ViewportRequest, ctx: BrowserCtxDep) -> dict[str, Any]:
    return _ok(
        await ctx.set_viewport(body.width, body.height, dpr=body.dpr), seq=ctx.seq
    )


@router.post("/cdp/send", response_model=OkEnvelope[CdpSendResponse])
async def handle_cdp_send(body: CdpSendRequest, ctx: BrowserCtxDep) -> dict[str, Any]:
    result = await ctx.raw_cdp(body.method, body.params, timeout=body.timeout)
    return _ok({"result": result}, seq=ctx.seq)


@router.post("/emulation", response_model=OkEnvelope[PageEmulation])
async def handle_emulation(
    body: EmulationRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    return _ok(await ctx.emulate(**body.model_dump()), seq=ctx.seq)
