"""Daemon-lifecycle routes: health, shutdown, launch, resume, cdp/endpoint.

These endpoints are about the daemon itself rather than browser actions.
``/health`` is unique in that it returns a flat ``HealthResponse`` instead
of going through ``OkEnvelope`` — the CLI/MCP doctor flows shipped before
the envelope was uniform and we keep the shape stable for compatibility.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from agentcloak.daemon.dependencies import (  # noqa: TC001
    ActiveTierDep,
    BrowserCtxDep,
    ConfigDep,
    ContextManagerDep,
    LocalProxyDep,
    OptionalBrowserCtxDep,
    RemoteCtxDep,
    ResumeWriterDep,
    ShutdownEventDep,
)
from agentcloak.daemon.models import (
    CDPEndpointResponse,
    HealthResponse,
    LaunchRequest,
    LaunchResponse,
    OkEnvelope,
    ResumeResponse,
    ShutdownResponse,
)
from agentcloak.daemon.routes._helpers import _ok
from agentcloak.daemon.services import DiagnosticService

__all__ = ["router"]

router = APIRouter()


# --- Health -----------------------------------------------------------------


@router.get("/health", response_model=HealthResponse)
async def handle_health(
    request: Request,
    ctx: OptionalBrowserCtxDep,
    local_proxy: LocalProxyDep,
    active_tier: ActiveTierDep,
    remote_ctx: RemoteCtxDep,
    config: ConfigDep,
) -> dict[str, Any]:
    # ``local_profile`` is recorded on app.state by ContextManager whenever a
    # local backend is activated with a profile name. Ephemeral sessions leave
    # it as None — doctor renders that as ``no profile (ephemeral)``.
    active_profile = getattr(request.app.state, "local_profile", None)
    route_count = len(request.app.routes)
    started_at = getattr(request.app.state, "started_at", None)
    metrics = getattr(request.app.state, "metrics", None)
    diagnostic = DiagnosticService()
    data = await diagnostic.health(
        ctx,
        local_proxy=local_proxy,
        active_tier=active_tier,
        remote_connected=remote_ctx is not None,
        config=config,
        active_profile=active_profile,
        route_count=route_count,
        started_at=started_at,
        metrics=metrics,
    )
    return data


# --- Shutdown ---------------------------------------------------------------


@router.post("/shutdown", response_model=OkEnvelope[ShutdownResponse])
async def handle_shutdown(event: ShutdownEventDep) -> dict[str, Any]:
    if event is not None:
        event.set()
    return _ok({}, seq=0)


# --- Launch (tier hot-switch) -----------------------------------------------


@router.post("/launch", response_model=OkEnvelope[LaunchResponse])
async def handle_launch(
    body: LaunchRequest,
    manager: ContextManagerDep,
) -> dict[str, Any]:
    """Hot-switch the active browser tier without restarting the daemon.

    ``cloak``/``playwright`` create or re-use a local browser; remote_bridge
    waits for the Chrome extension to connect (if it isn't already).
    """
    from agentcloak.core.config import resolve_tier
    from agentcloak.core.types import StealthTier

    resolved = resolve_tier(body.tier)
    try:
        tier_enum = StealthTier(resolved)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "error": "invalid_tier",
                "hint": f"Unknown tier: {body.tier!r}",
                "action": "use one of: auto, cloak, playwright, remote_bridge",
            },
        ) from exc

    result = await manager.switch_tier(tier_enum, profile=body.profile)
    return _ok(result, seq=0)


# --- Resume -----------------------------------------------------------------


@router.get("/resume", response_model=OkEnvelope[ResumeResponse])
async def handle_resume(
    ctx: BrowserCtxDep,
    resume_writer: ResumeWriterDep,
) -> dict[str, Any]:
    if resume_writer is None:
        raise HTTPException(
            status_code=503,
            detail={
                "ok": False,
                "error": "resume_unavailable",
                "hint": "Resume writer not initialized",
                "action": "restart the daemon",
            },
        )
    # Persisted resume snapshot only updates on navigate/action (via
    # _update_resume). Runtime-mutable fields like ``capture_active`` and
    # ``stealth_tier`` need to be re-read from the live context, otherwise
    # ``resume`` returns stale values when the agent toggled capture between
    # actions (dogfood F2). ``page_valid`` flips on every navigate
    # attempt so it must always come from the live context, never from
    # the persisted snapshot.
    data = resume_writer.current_snapshot.to_dict()
    data["capture_active"] = ctx.capture_store.recording
    data["stealth_tier"] = ctx.stealth_tier.value
    data["page_valid"] = bool(getattr(ctx, "_page_valid", True))
    return _ok(data, seq=ctx.seq)


# --- CDP --------------------------------------------------------------------


@router.get("/cdp/endpoint", response_model=OkEnvelope[CDPEndpointResponse])
async def handle_cdp_endpoint(ctx: BrowserCtxDep) -> dict[str, Any]:
    """Return the CDP WebSocket URL for jshookmcp browser_attach."""
    import httpx

    cdp_port: int | None = getattr(ctx, "_cdp_port", None)
    if not cdp_port:
        raise HTTPException(
            status_code=503,
            detail={
                "ok": False,
                "error": "no_cdp_port",
                "hint": "No CDP port available",
                "action": "restart daemon — CDP port is allocated at browser launch",
            },
        )

    http_url = f"http://127.0.0.1:{cdp_port}"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{http_url}/json/version")
            info = resp.json()
        ws_endpoint: str = info.get("webSocketDebuggerUrl", "")
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "ok": False,
                "error": "cdp_unreachable",
                "hint": f"DevTools HTTP API at port {cdp_port} unreachable: {exc}",
                "action": "ensure browser is running and CDP port is open",
            },
        ) from exc

    data = {"ws_endpoint": ws_endpoint, "http_url": http_url, "port": cdp_port}
    return _ok(data, seq=ctx.seq)
