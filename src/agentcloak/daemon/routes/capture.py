"""Capture routes — record, inspect, export, analyse, replay browser traffic.

Capture is fully delegated to :class:`CaptureService`; routes are just
HTTP envelopes around it. Start/stop also poke the backend's
``_capture_setup_impl`` hook so RemoteBridge can enable ``Network.enable``
on the CDP target.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from agentcloak.daemon.dependencies import BrowserCtxDep  # noqa: TC001
from agentcloak.daemon.models import (
    CaptureAnalyzeResponse,
    CaptureClearResponse,
    CaptureExportResponse,
    CaptureReplayRequest,
    CaptureReplayResponse,
    CaptureStatusResponse,
    OkEnvelope,
)
from agentcloak.daemon.routes._helpers import _ok
from agentcloak.daemon.services import CaptureService

__all__ = ["router"]

router = APIRouter()


@router.post("/capture/start", response_model=OkEnvelope[CaptureStatusResponse])
async def handle_capture_start(ctx: BrowserCtxDep) -> dict[str, Any]:
    # ctx.capture_start() runs the backend's ``_capture_setup_impl`` hook
    # (no-op for Playwright, ``Network.enable`` for RemoteBridge) so capture
    # works uniformly across both backends.
    result = await ctx.capture_start()
    return _ok(result, seq=ctx.seq)


@router.post("/capture/stop", response_model=OkEnvelope[CaptureStatusResponse])
async def handle_capture_stop(ctx: BrowserCtxDep) -> dict[str, Any]:
    result = await ctx.capture_stop()
    return _ok(result, seq=ctx.seq)


@router.get("/capture/status", response_model=OkEnvelope[CaptureStatusResponse])
async def handle_capture_status(ctx: BrowserCtxDep) -> dict[str, Any]:
    service = CaptureService(ctx.capture_store)
    result = service.status()
    return _ok(result, seq=ctx.seq)


@router.get("/capture/export", response_model=OkEnvelope[CaptureExportResponse])
async def handle_capture_export(
    ctx: BrowserCtxDep, format: str = "har"
) -> dict[str, Any]:
    service = CaptureService(ctx.capture_store)
    result = service.export(fmt=format)
    return _ok(result, seq=ctx.seq)


@router.get("/capture/analyze", response_model=OkEnvelope[CaptureAnalyzeResponse])
async def handle_capture_analyze(
    ctx: BrowserCtxDep, domain: str | None = None
) -> dict[str, Any]:
    service = CaptureService(ctx.capture_store)
    try:
        result = service.analyze(domain=domain)
        return _ok(result, seq=ctx.seq)
    except Exception as exc:
        from agentcloak.daemon.services.capture_service import CaptureReplayError

        if isinstance(exc, CaptureReplayError):
            raise HTTPException(status_code=500, detail=exc.to_dict()) from exc
        raise


@router.post("/capture/clear", response_model=OkEnvelope[CaptureClearResponse])
async def handle_capture_clear(ctx: BrowserCtxDep) -> dict[str, Any]:
    service = CaptureService(ctx.capture_store)
    result = service.clear()
    return _ok(result, seq=ctx.seq)


@router.post("/capture/replay", response_model=OkEnvelope[CaptureReplayResponse])
async def handle_capture_replay(
    body: CaptureReplayRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    from agentcloak.daemon.services.capture_service import CaptureReplayError

    service = CaptureService(ctx.capture_store)
    try:
        result = await service.replay(ctx, url=body.url, method=body.method)
    except CaptureReplayError as exc:
        status = {
            "missing_url": 400,
            "capture_entry_not_found": 404,
        }.get(exc.error, 400)
        raise HTTPException(status_code=status, detail=exc.to_dict()) from exc
    return _ok(result, seq=ctx.seq)
