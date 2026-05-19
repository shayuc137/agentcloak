"""FastAPI route definitions — thin shells over the service layer.

Each route handler does three things:
1. parse the Pydantic request body
2. delegate to a service in :mod:`agentcloak.daemon.services`
3. wrap the service's return value in the ``OkEnvelope`` shape

Business logic (stale-ref retry, snapshot diff, profile CRUD, capture export,
doctor checks) lives in the services. Routes intentionally avoid framework
specifics — when something goes wrong they either raise
:class:`AgentBrowserError` (caught by the global handler) or
:class:`HTTPException` with a structured detail dict.
"""

from __future__ import annotations

from typing import Any

import orjson
import structlog
from fastapi import (
    APIRouter,
    HTTPException,
    Request,
    WebSocket,
)
from fastapi.responses import PlainTextResponse, Response

# ``screenshot_to_base64`` lives on the base browser module — importing it
# here keeps daemon code from depending on a specific backend
# (``playwright_ctx``), which would bypass the layer-isolation rule that
# ``daemon/`` only talks to the abstract :class:`BrowserContextBase`.
from agentcloak.browser.base import screenshot_to_base64
from agentcloak.core.errors import ProfileError

# Annotated dependency aliases (BrowserCtxDep etc.) must be available at
# runtime so FastAPI can resolve `Depends()` markers when registering routes —
# placing them under TYPE_CHECKING would break the framework.
from agentcloak.daemon.dependencies import (  # noqa: TC001
    ActiveTierDep,
    BridgeServiceDep,
    BrowserCtxDep,
    ConfigDep,
    ContextManagerDep,
    LocalProxyDep,
    OptionalBrowserCtxDep,
    RemoteCtxDep,
    RequiredRemoteCtxDep,
    ResumeWriterDep,
    ShutdownEventDep,
    SnapshotCacheDep,
)

# Pydantic Request *and* Response models must be runtime-resolvable so
# FastAPI can build OpenAPI schemas at startup — keep them out of
# TYPE_CHECKING. Each route declares ``response_model=OkEnvelope[XxxResponse]``
# (or a flat ``HealthResponse`` for the un-enveloped endpoints), which is
# what feeds the auto-generated OpenAPI spec consumed by T8.
from agentcloak.daemon.models import (
    ActionRequest,
    ActionResponse,
    BatchActionRequest,
    BatchActionResponse,
    BridgeClaimRequest,
    BridgeFinalizeRequest,
    BridgeOpResponse,
    BridgeTokenResetResponse,
    CaptureAnalyzeResponse,
    CaptureClearResponse,
    CaptureExportResponse,
    CaptureReplayRequest,
    CaptureReplayResponse,
    CaptureStatusResponse,
    CDPEndpointResponse,
    CookiesExportRequest,
    CookiesExportResponse,
    CookiesImportRequest,
    CookiesImportResponse,
    DialogHandleRequest,
    DialogHandleResponse,
    DialogStatusResponse,
    EvaluateRequest,
    EvaluateResponse,
    FetchRequest,
    FetchResponse,
    FrameFocusRequest,
    FrameFocusResponse,
    FrameListResponse,
    HealthResponse,
    LaunchRequest,
    LaunchResponse,
    NavigateRequest,
    NavigateResponse,
    NetworkResponse,
    OkEnvelope,
    ProfileCreateFromCurrentRequest,
    ProfileCreateFromCurrentResponse,
    ProfileCreateRequest,
    ProfileCreateResponse,
    ProfileDeleteRequest,
    ProfileListResponse,
    ResumeResponse,
    ScreenshotResponse,
    ShutdownResponse,
    SnapshotResponse,
    SpellListResponse,
    SpellRunRequest,
    SpellRunResponse,
    TabCloseRequest,
    TabListResponse,
    TabNewRequest,
    TabOpResponse,
    TabSwitchRequest,
    UploadRequest,
    UploadResponse,
    WaitRequest,
    WaitResponse,
)
from agentcloak.daemon.services import (
    ActionService,
    CaptureService,
    DiagnosticService,
    ProfileService,
    SnapshotService,
)
from agentcloak.daemon.text_renderers import (
    render_action_text,
    render_capture_analyze_text,
    render_capture_status_text,
    render_cdp_endpoint_text,
    render_cookies_export_text,
    render_cookies_import_text,
    render_dialog_handle_text,
    render_dialog_status_text,
    render_evaluate_text,
    render_fetch_text,
    render_frame_focus_text,
    render_frame_list_text,
    render_health_text,
    render_launch_text,
    render_navigate_text,
    render_network_text,
    render_profile_list_text,
    render_resume_text,
    render_screenshot_text,
    render_snapshot_text,
    render_spell_list_text,
    render_spell_run_text,
    render_tab_list_text,
    render_tab_op_text,
    render_upload_text,
    render_wait_text,
    wants_text,
)

logger = structlog.get_logger()

__all__ = [
    "register_routers",
    "router",
]

router = APIRouter()


def _ok(data: Any, *, seq: int) -> dict[str, Any]:
    """Wrap a payload in the success envelope shared with the OkEnvelope model."""
    return {"ok": True, "seq": seq, "data": data}


def _profile_error_to_http(exc: ProfileError) -> HTTPException:
    """Translate a ProfileError into a FastAPI HTTPException with the right status."""
    status_map = {
        "missing_name": 400,
        "invalid_profile_name": 400,
        "invalid_profile_path": 400,
        "profile_exists": 409,
        "profile_not_found": 404,
        "profile_writer_failed": 500,
    }
    return HTTPException(
        status_code=status_map.get(exc.error, 400),
        detail=exc.to_dict(),
    )


def _profiles_dir():  # type: ignore[no-untyped-def]
    """Load the profiles directory from the daemon config snapshot."""
    from agentcloak.core.config import load_config

    paths, _ = load_config()
    return paths.profiles_dir


# --- Helpers ----------------------------------------------------------------


async def _update_resume(
    writer: Any,
    ctx: Any,
    *,
    action_summary: dict[str, Any] | None = None,
) -> None:
    """Mark resume snapshot dirty (non-blocking, background task flushes).

    ``writer`` is the :class:`ResumeWriter` injected via :class:`ResumeWriterDep`
    by the calling route. ``ctx`` exposes the live session data through
    :meth:`BrowserContextBase.resume_snapshot`, so this helper never has to
    introspect backend internals.
    """
    if writer is None:
        return

    snap: dict[str, Any]
    try:
        snap = await ctx.resume_snapshot()
    except Exception:
        logger.debug("resume_state_extraction_failed", exc_info=True)
        snap = {
            "url": "",
            "title": "",
            "tabs": [],
            "capture_active": ctx.capture_store.recording,
            "stealth_tier": ctx.stealth_tier.value,
        }

    writer.mark_dirty(
        url=str(snap.get("url", "")),
        title=str(snap.get("title", "")),
        tabs=list(snap.get("tabs", []) or []),
        action_summary=action_summary,
        capture_active=bool(snap.get("capture_active", False)),
        stealth_tier=str(snap.get("stealth_tier", "")),
    )


# --- Health -----------------------------------------------------------------


@router.get("/health", response_model=HealthResponse)
async def handle_health(
    ctx: OptionalBrowserCtxDep,
    request: Request,
    local_proxy: LocalProxyDep,
    active_tier: ActiveTierDep,
    remote_ctx: RemoteCtxDep,
) -> Response | dict[str, Any]:
    diagnostic = DiagnosticService()
    data = await diagnostic.health(
        ctx,
        local_proxy=local_proxy,
        active_tier=active_tier,
        remote_connected=remote_ctx is not None,
    )
    if wants_text(request):
        return PlainTextResponse(render_health_text(data))
    return data


# --- Navigate ---------------------------------------------------------------


@router.post("/navigate", response_model=OkEnvelope[NavigateResponse])
async def handle_navigate(
    body: NavigateRequest,
    ctx: BrowserCtxDep,
    config: ConfigDep,
    resume_writer: ResumeWriterDep,
    request: Request,
) -> Response | dict[str, Any]:
    result = await ctx.navigate(body.url, timeout=body.timeout)
    await _update_resume(
        resume_writer, ctx, action_summary={"kind": "navigate", "url": body.url}
    )

    if body.include_snapshot:
        try:
            # Match the ``/snapshot`` route's default-cap behaviour so the
            # ``--snap`` hot path doesn't accidentally dump 200+ node trees on
            # busy sites. Compact mode honours config.snapshot_max_nodes (80);
            # other modes are explicit asks for the full payload and stay
            # uncapped — same semantics as ``handle_snapshot``.
            attach_max = (
                config.snapshot_max_nodes if body.snapshot_mode == "compact" else 0
            )
            snap = await ctx.snapshot(mode=body.snapshot_mode, max_nodes=attach_max)
            SnapshotService.attach_snapshot_to_result(result, snap)
        except Exception:
            logger.debug("include_snapshot_failed", exc_info=True)

    if wants_text(request):
        return PlainTextResponse(render_navigate_text(result))
    return _ok(result, seq=ctx.seq)


# --- Screenshot -------------------------------------------------------------


@router.get("/screenshot", response_model=OkEnvelope[ScreenshotResponse])
async def handle_screenshot(
    ctx: BrowserCtxDep,
    request: Request,
    config: ConfigDep,
    full_page: bool = False,
    format: str = "jpeg",
    quality: int | None = None,
) -> Response | dict[str, Any]:
    # ``quality=None`` resolves to the configured default. CLI callers leave
    # this unset and inherit the file/env default; MCP tools pass an explicit
    # lower value so screenshots stay under MCP token budgets.
    if quality is None:
        quality = config.screenshot_quality
    raw = await ctx.screenshot(full_page=full_page, format=format, quality=quality)
    b64 = screenshot_to_base64(raw)
    data = {"base64": b64, "size": len(raw), "format": format}
    if wants_text(request):
        # CLI command writes the bytes to a temp file before calling text — but
        # daemon doesn't see the user filesystem. Best we can do here is the
        # summary line; the CLI's text-mode screenshot helper composes the
        # final path message itself.
        return PlainTextResponse(render_screenshot_text(data))
    return _ok(data, seq=ctx.seq)


# --- Snapshot ---------------------------------------------------------------


@router.get("/snapshot", response_model=OkEnvelope[SnapshotResponse])
async def handle_snapshot(
    ctx: BrowserCtxDep,
    request: Request,
    config: ConfigDep,
    snapshot_cache: SnapshotCacheDep,
    mode: str = "compact",
    max_nodes: int = -1,
    max_chars: int = 0,
    focus: int = 0,
    offset: int = 0,
    include_selector_map: bool = False,
    frames: bool = False,
    diff: bool = False,
) -> Response | dict[str, Any]:
    # ``max_nodes`` semantics:
    #   * ``-1`` (route default) — caller didn't specify; in compact mode we
    #     fall back to ``config.snapshot_max_nodes`` so busy pages (HN front
    #     hits ~230 interactive nodes) don't blow the agent's context budget
    #   * ``0`` — caller explicitly opts into the full tree (``--limit 0``)
    #   * ``>0`` — explicit cap
    # ``accessible``/``dom``/``content`` modes are explicit asks for the full
    # payload, so we never auto-cap them; ``-1`` collapses to ``0`` there.
    if max_nodes == -1:
        effective_max_nodes = config.snapshot_max_nodes if mode == "compact" else 0
    else:
        effective_max_nodes = max_nodes

    service = SnapshotService()

    data, cur_cache = await service.get(
        ctx,
        mode=mode,
        max_nodes=effective_max_nodes,
        max_chars=max_chars,
        focus=focus,
        offset=offset,
        include_selector_map=include_selector_map,
        frames=frames,
        diff=diff,
        prev_cached_lines=snapshot_cache.prev_lines,
    )

    if cur_cache is not None:
        snapshot_cache.prev_lines = cur_cache

    # Surface seq for the text renderer header line ("seq=N"). The JSON
    # envelope already carries it in the wrapper.
    data["seq"] = ctx.seq
    if wants_text(request):
        return PlainTextResponse(render_snapshot_text(data))
    # Pop the redundant seq field from the inner data — it's already in the
    # envelope and double-emitting would change the JSON shape.
    data.pop("seq", None)
    return _ok(data, seq=ctx.seq)


# --- Evaluate ---------------------------------------------------------------


@router.post("/evaluate", response_model=OkEnvelope[EvaluateResponse])
async def handle_evaluate(
    body: EvaluateRequest, ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    result = await ctx.evaluate(body.js, world=body.world)

    # Truncate large results before they exceed MCP token limits.
    result_bytes = orjson.dumps(result)
    total_size = len(result_bytes)
    if total_size > body.max_return_size:
        result_repr = (
            result_bytes[: body.max_return_size].decode("utf-8", errors="replace")
            + "\n[...truncated...]"
        )
        data = {"result": result_repr, "truncated": True, "total_size": total_size}
        if wants_text(request):
            return PlainTextResponse(render_evaluate_text(data))
        return _ok(data, seq=ctx.seq)

    data = {"result": result, "truncated": False, "total_size": total_size}
    if wants_text(request):
        return PlainTextResponse(render_evaluate_text(data))
    return _ok(data, seq=ctx.seq)


# --- Network ----------------------------------------------------------------


@router.get("/network", response_model=OkEnvelope[NetworkResponse])
async def handle_network(
    ctx: BrowserCtxDep, request: Request, since: str = "0"
) -> Response | dict[str, Any]:
    since_value: int | str = int(since) if since.isdigit() else since
    reqs = await ctx.network(since=since_value)
    data = {"requests": reqs, "count": len(reqs)}
    if wants_text(request):
        return PlainTextResponse(render_network_text(data))
    return _ok(data, seq=ctx.seq)


# --- Action -----------------------------------------------------------------


@router.post("/action", response_model=OkEnvelope[ActionResponse])
async def handle_action(
    body: ActionRequest,
    ctx: BrowserCtxDep,
    config: ConfigDep,
    resume_writer: ResumeWriterDep,
    request: Request,
) -> Response | dict[str, Any]:
    target = str(body.index) if body.index is not None else body.target
    extra = body.model_dump(exclude_unset=True)
    for known in ("kind", "index", "target", "include_snapshot", "snapshot_mode"):
        extra.pop(known, None)

    service = ActionService()
    # DialogBlockedError raised from ctx.action() bubbles up to the FastAPI
    # exception handler (409 with dialog metadata) — no special case needed.
    result, retried = await service.execute(ctx, body.kind, target, extra=extra)
    if retried:
        result["retried"] = True

    summary: dict[str, Any] = {"kind": body.kind, "target": target}
    if body.kind in ("fill", "type"):
        summary["text"] = extra.get("text", "")
    elif body.kind in ("press", "keydown", "keyup"):
        summary["key"] = extra.get("key", "")
    elif body.kind == "scroll":
        summary["direction"] = extra.get("direction", "down")
    elif body.kind == "select":
        summary["value"] = extra.get("value", "")
    await _update_resume(resume_writer, ctx, action_summary=summary)

    if body.include_snapshot:
        # When the action caused a navigation, the page is still loading at
        # this point — ``_post_action_cleanup`` only waits 2s for DOM ready,
        # and that timeout is suppressed. Take a focused wait here so the
        # attached snapshot reflects the new page, not the in-flight loader.
        if result.get("caused_navigation"):
            try:
                await ctx.wait(
                    condition="load",
                    value="domcontentloaded",
                    timeout=10000,
                )
            except Exception:
                logger.debug("post_navigation_wait_failed", exc_info=True)
        try:
            # Same default-cap rule as ``/snapshot`` and ``/navigate?include_snapshot``:
            # the ``--snap`` hot path is what most agents drive, so the default
            # node budget must mirror the standalone snapshot route or busy
            # pages dump 200+ nodes through ``--snap`` while ``cloak snapshot``
            # caps at 80.
            attach_max = (
                config.snapshot_max_nodes if body.snapshot_mode == "compact" else 0
            )
            snap = await ctx.snapshot(mode=body.snapshot_mode, max_nodes=attach_max)
            SnapshotService.attach_snapshot_to_result(result, snap)
        except Exception:
            logger.debug("include_snapshot_failed", exc_info=True)

    if wants_text(request):
        # Surface the parameters the renderer needs without polluting the
        # JSON envelope (where they'd duplicate the request body).
        result["text"] = extra.get("text", result.get("text", ""))
        result["key"] = extra.get("key", result.get("key", ""))
        return PlainTextResponse(render_action_text(body.kind, target, result))
    return _ok(result, seq=ctx.seq)


@router.post("/action/batch", response_model=OkEnvelope[BatchActionResponse])
async def handle_action_batch(
    body: BatchActionRequest,
    ctx: BrowserCtxDep,
    config: ConfigDep,
    request: Request,
) -> Response | dict[str, Any]:
    # ``batch_settle_timeout`` is just a config knob — the in-process override
    # used to live on ``app.state.batch_settle_timeout`` (set by
    # ``configure_app_state``) so tests could tweak it without re-loading the
    # whole config. Since the override always lands in ``config`` already, read
    # directly from there and skip the parallel ``app.state`` slot.
    settle_timeout = body.settle_timeout or config.batch_settle_timeout

    service = ActionService()
    result = await service.execute_batch(
        ctx, body.actions, sleep_s=body.sleep, settle_timeout=settle_timeout
    )
    if wants_text(request):
        completed = int(result.get("completed", 0) or 0)
        total = int(result.get("total", 0) or 0)
        aborted = result.get("aborted_reason", "")
        line = f"batch: {completed}/{total} completed"
        if aborted:
            line += f" | aborted: {aborted}"
        return PlainTextResponse(line)
    return _ok(result, seq=ctx.seq)


# --- Fetch ------------------------------------------------------------------


@router.post("/fetch", response_model=OkEnvelope[FetchResponse])
async def handle_fetch(
    body: FetchRequest, ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    result = await ctx.fetch(
        body.url,
        method=body.method,
        body=body.body,
        headers=body.headers,
        timeout=body.timeout,
    )
    if wants_text(request):
        return PlainTextResponse(render_fetch_text(result))
    return _ok(result, seq=ctx.seq)


# --- Shutdown ---------------------------------------------------------------


@router.post("/shutdown", response_model=OkEnvelope[ShutdownResponse])
async def handle_shutdown(
    request: Request, event: ShutdownEventDep
) -> Response | dict[str, Any]:
    if event is not None:
        event.set()
    if wants_text(request):
        return PlainTextResponse("stopped")
    return _ok({}, seq=0)


# --- Launch (tier hot-switch) -----------------------------------------------


@router.post("/launch", response_model=OkEnvelope[LaunchResponse])
async def handle_launch(
    body: LaunchRequest,
    manager: ContextManagerDep,
    request: Request,
) -> Response | dict[str, Any]:
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
    if wants_text(request):
        return PlainTextResponse(render_launch_text(result))
    return _ok(result, seq=0)


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


# --- Cookies ----------------------------------------------------------------


@router.post("/cookies/export", response_model=OkEnvelope[CookiesExportResponse])
async def handle_cookies_export(
    body: CookiesExportRequest,
    ctx: BrowserCtxDep,
    remote_ctx: RemoteCtxDep,
    request: Request,
) -> Response | dict[str, Any]:
    if remote_ctx is not None:
        from agentcloak.browser.remote_ctx import RemoteBridgeContext

        if not isinstance(remote_ctx, RemoteBridgeContext):
            raise RuntimeError("remote_ctx is not a RemoteBridgeContext instance")
        params: dict[str, Any] = {}
        if body.url:
            params["url"] = body.url
        result = await remote_ctx.send_command("cookies", params)
        count = len(result) if isinstance(result, list) else 0
        data = {"cookies": result, "count": count}
        if wants_text(request):
            return PlainTextResponse(render_cookies_export_text(data))
        return _ok({"cookies": result}, seq=0)

    browser_context = ctx._get_browser_context()
    if body.url:
        cookies = await browser_context.cookies(body.url)
    else:
        cookies = await browser_context.cookies()
    # Field names use camelCase (httpOnly, sameSite) because these are passed
    # straight through from the Playwright / CDP Cookie spec — re-serializing
    # to snake_case would force agents to translate twice when feeding cookies
    # back into ``cookies/import`` or generic devtools clients.
    serializable = [
        {
            "name": c.get("name", ""),
            "value": c.get("value", ""),
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
            "expires": c.get("expires", -1),
            "httpOnly": c.get("httpOnly", False),
            "secure": c.get("secure", False),
            "sameSite": c.get("sameSite", "None"),
        }
        for c in cookies
    ]
    data = {"cookies": serializable, "count": len(serializable)}
    if wants_text(request):
        return PlainTextResponse(render_cookies_export_text(data))
    return _ok(data, seq=ctx.seq)


@router.post("/cookies/import", response_model=OkEnvelope[CookiesImportResponse])
async def handle_cookies_import(
    body: CookiesImportRequest, ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    if not body.cookies:
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "error": "no_cookies",
                "hint": "No cookies provided",
                "action": "pass cookies as JSON array in 'cookies' field",
            },
        )
    browser_context = ctx._get_browser_context()
    await browser_context.add_cookies(body.cookies)
    data = {"imported": len(body.cookies)}
    if wants_text(request):
        return PlainTextResponse(render_cookies_import_text(data))
    return _ok(data, seq=ctx.seq)


# --- Capture ----------------------------------------------------------------


@router.post("/capture/start", response_model=OkEnvelope[CaptureStatusResponse])
async def handle_capture_start(
    ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    # ctx.capture_start() runs the backend's ``_capture_setup_impl`` hook
    # (no-op for Playwright, ``Network.enable`` for RemoteBridge) so capture
    # works uniformly across both backends.
    result = await ctx.capture_start()
    if wants_text(request):
        return PlainTextResponse(render_capture_status_text(result))
    return _ok(result, seq=ctx.seq)


@router.post("/capture/stop", response_model=OkEnvelope[CaptureStatusResponse])
async def handle_capture_stop(
    ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    result = await ctx.capture_stop()
    if wants_text(request):
        return PlainTextResponse(render_capture_status_text(result))
    return _ok(result, seq=ctx.seq)


@router.get("/capture/status", response_model=OkEnvelope[CaptureStatusResponse])
async def handle_capture_status(
    ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    service = CaptureService(ctx.capture_store)
    result = service.status()
    if wants_text(request):
        return PlainTextResponse(render_capture_status_text(result))
    return _ok(result, seq=ctx.seq)


@router.get("/capture/export", response_model=OkEnvelope[CaptureExportResponse])
async def handle_capture_export(
    ctx: BrowserCtxDep, request: Request, format: str = "har"
) -> Response | dict[str, Any]:
    service = CaptureService(ctx.capture_store)
    result = service.export(fmt=format)
    if wants_text(request):
        # HAR/JSON is structured data — emit it pretty-printed so the user
        # can pipe it straight to a file.
        body = orjson.dumps(result, option=orjson.OPT_INDENT_2).decode()
        return PlainTextResponse(body)
    return _ok(result, seq=ctx.seq)


@router.get("/capture/analyze", response_model=OkEnvelope[CaptureAnalyzeResponse])
async def handle_capture_analyze(
    ctx: BrowserCtxDep, request: Request, domain: str | None = None
) -> Response | dict[str, Any]:
    service = CaptureService(ctx.capture_store)
    try:
        result = service.analyze(domain=domain)
        if wants_text(request):
            return PlainTextResponse(render_capture_analyze_text(result))
        return _ok(result, seq=ctx.seq)
    except Exception as exc:
        from agentcloak.daemon.services.capture_service import CaptureReplayError

        if isinstance(exc, CaptureReplayError):
            raise HTTPException(status_code=500, detail=exc.to_dict()) from exc
        raise


@router.post("/capture/clear", response_model=OkEnvelope[CaptureClearResponse])
async def handle_capture_clear(
    ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    service = CaptureService(ctx.capture_store)
    result = service.clear()
    if wants_text(request):
        # PRD R2: ``cleared {n} entries`` — surface the count so agents see
        # what they wiped without a follow-up status check.
        n = int(result.get("entries", 0) or 0)
        return PlainTextResponse(f"cleared {n} entries")
    return _ok(result, seq=ctx.seq)


@router.post("/capture/replay", response_model=OkEnvelope[CaptureReplayResponse])
async def handle_capture_replay(
    body: CaptureReplayRequest, ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
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
    if wants_text(request):
        # Replay returns an HTTP-style response; bare-body matches fetch.
        return PlainTextResponse(render_fetch_text(result))
    return _ok(result, seq=ctx.seq)


# --- CDP --------------------------------------------------------------------


@router.get("/cdp/endpoint", response_model=OkEnvelope[CDPEndpointResponse])
async def handle_cdp_endpoint(
    ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
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
    if wants_text(request):
        return PlainTextResponse(render_cdp_endpoint_text(data))
    return _ok(data, seq=ctx.seq)


# --- Tabs -------------------------------------------------------------------


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


# --- Resume -----------------------------------------------------------------


@router.get("/resume", response_model=OkEnvelope[ResumeResponse])
async def handle_resume(
    ctx: BrowserCtxDep,
    resume_writer: ResumeWriterDep,
    request: Request,
) -> Response | dict[str, Any]:
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
    # actions (dogfood F2).
    data = resume_writer.current_snapshot.to_dict()
    data["capture_active"] = ctx.capture_store.recording
    data["stealth_tier"] = ctx.stealth_tier.value
    if wants_text(request):
        return PlainTextResponse(render_resume_text(data))
    return _ok(data, seq=ctx.seq)


# --- Spells -----------------------------------------------------------------


@router.post("/spell/run", response_model=OkEnvelope[SpellRunResponse])
async def handle_spell_run(
    body: SpellRunRequest, ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    """Run a registered spell with the daemon's live browser context."""
    from agentcloak.spells.discovery import discover_spells
    from agentcloak.spells.executor import execute_spell
    from agentcloak.spells.registry import get_registry

    parts = body.name.split("/", 1)
    if len(parts) != 2:
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "error": "invalid_spell_name",
                "hint": f"Expected 'site/command', got '{body.name}'",
                "action": "use format like 'httpbin/headers'",
            },
        )

    discover_spells()
    registry = get_registry()
    entry = registry.get(parts[0], parts[1])
    if entry is None:
        available = [e.meta.full_name for e in registry.list_all()]
        raise HTTPException(
            status_code=404,
            detail={
                "ok": False,
                "error": "spell_not_found",
                "hint": f"No spell '{body.name}'",
                "action": f"available: {', '.join(available[:10])}",
            },
        )

    result = await execute_spell(entry, args=body.args, browser=ctx)
    data = {"result": result}
    if wants_text(request):
        return PlainTextResponse(render_spell_run_text(data))
    return _ok(data, seq=ctx.seq)


@router.get("/spell/list", response_model=OkEnvelope[SpellListResponse])
async def handle_spell_list(
    ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    """List all registered spells."""
    from agentcloak.spells.discovery import discover_spells
    from agentcloak.spells.registry import get_registry

    discover_spells()
    registry = get_registry()
    spells = [
        {
            "full_name": e.meta.full_name,
            "strategy": e.meta.strategy.value,
            "access": e.meta.access,
            "description": e.meta.description,
        }
        for e in registry.list_all()
    ]
    data = {"spells": spells, "count": len(spells)}
    if wants_text(request):
        return PlainTextResponse(render_spell_list_text(data))
    return _ok(data, seq=ctx.seq)


# --- Profile ----------------------------------------------------------------


@router.post(
    "/profile/create-from-current",
    response_model=OkEnvelope[ProfileCreateFromCurrentResponse],
)
async def handle_profile_create_from_current(
    body: ProfileCreateFromCurrentRequest,
    ctx: BrowserCtxDep,
    remote_ctx: RemoteCtxDep,
    request: Request,
) -> Response | dict[str, Any]:
    """Create a profile from the current browser session's cookies."""
    service = ProfileService(_profiles_dir())

    try:
        service.validate_name(body.name)
    except ProfileError as exc:
        raise _profile_error_to_http(exc) from exc

    cookies: list[dict[str, Any]]
    if remote_ctx is not None:
        from agentcloak.browser.remote_ctx import RemoteBridgeContext

        if not isinstance(remote_ctx, RemoteBridgeContext):
            raise RuntimeError("remote_ctx is not a RemoteBridgeContext instance")
        # The bridge ``cookies`` command returns either a list of cookie dicts
        # directly or a ``{"cookies": [...]}`` envelope depending on extension
        # version. Normalise to a list either way.
        raw_response: Any = await remote_ctx.send_command("cookies", {})
        cookies = []
        if isinstance(raw_response, list):
            cookies = list(raw_response)  # type: ignore[arg-type]
        elif isinstance(raw_response, dict):
            inner = raw_response.get("cookies", [])  # type: ignore[arg-type]
            if isinstance(inner, list):
                cookies = list(inner)  # type: ignore[arg-type]
    else:
        browser_context = ctx._get_browser_context()
        cookies = await browser_context.cookies()

    try:
        result = await service.create_from_cookies(body.name, cookies)
    except ProfileError as exc:
        raise _profile_error_to_http(exc) from exc
    if wants_text(request):
        return PlainTextResponse(
            f'created profile "{result.get("profile", body.name)}" '
            f"({int(result.get('cookie_count', 0) or 0)} cookies)"
        )
    return _ok(result, seq=ctx.seq)


@router.get("/profile/list", response_model=OkEnvelope[ProfileListResponse])
async def handle_profile_list(
    ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    service = ProfileService(_profiles_dir())
    names = service.list_profiles()
    data = {"profiles": names, "count": len(names)}
    if wants_text(request):
        return PlainTextResponse(render_profile_list_text(data))
    return _ok(data, seq=ctx.seq)


@router.post("/profile/create", response_model=OkEnvelope[ProfileCreateResponse])
async def handle_profile_create(
    body: ProfileCreateRequest, ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    service = ProfileService(_profiles_dir())
    try:
        name = service.create(body.name)
    except ProfileError as exc:
        raise _profile_error_to_http(exc) from exc
    if wants_text(request):
        return PlainTextResponse(f'created profile "{name}"')
    return _ok({"created": name}, seq=ctx.seq)


@router.post("/profile/delete", response_model=OkEnvelope[ProfileCreateResponse])
async def handle_profile_delete(
    body: ProfileDeleteRequest, ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    service = ProfileService(_profiles_dir())
    try:
        name = service.delete(body.name)
    except ProfileError as exc:
        raise _profile_error_to_http(exc) from exc
    if wants_text(request):
        return PlainTextResponse(f'deleted profile "{name}"')
    return _ok({"deleted": name}, seq=ctx.seq)


# --- Dialog -----------------------------------------------------------------


@router.get("/dialog/status", response_model=OkEnvelope[DialogStatusResponse])
async def handle_dialog_status(
    ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    dialog = await ctx.dialog_status()
    if dialog is None:
        data = {"pending": False}
        if wants_text(request):
            return PlainTextResponse(render_dialog_status_text(data))
        return _ok(data, seq=ctx.seq)
    data = {
        "pending": True,
        "dialog": {
            "type": dialog.dialog_type,
            "message": dialog.message,
            "default_value": dialog.default_value,
            "url": dialog.url,
        },
    }
    if wants_text(request):
        return PlainTextResponse(render_dialog_status_text(data))
    return _ok(data, seq=ctx.seq)


@router.post("/dialog/handle", response_model=OkEnvelope[DialogHandleResponse])
async def handle_dialog_handle(
    body: DialogHandleRequest, ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    result = await ctx.dialog_handle(body.action, text=body.text)
    # Echo the requested action back so the renderer knows what we did.
    result.setdefault("action", body.action)
    if wants_text(request):
        return PlainTextResponse(render_dialog_handle_text(result))
    return _ok(result, seq=ctx.seq)


# --- Wait -------------------------------------------------------------------


@router.post("/wait", response_model=OkEnvelope[WaitResponse])
async def handle_wait(
    body: WaitRequest, ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    result = await ctx.wait(
        condition=body.condition,
        value=body.value,
        timeout=body.timeout,
        state=body.state,
    )
    # Surface the condition/value the renderer needs without forcing the
    # browser layer to echo them back.
    result.setdefault("condition", body.condition)
    result.setdefault("value", body.value)
    if wants_text(request):
        return PlainTextResponse(render_wait_text(result))
    return _ok(result, seq=ctx.seq)


# --- Upload -----------------------------------------------------------------


@router.post("/upload", response_model=OkEnvelope[UploadResponse])
async def handle_upload(
    body: UploadRequest, ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    if not body.files:
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "error": "missing_files",
                "hint": "No files provided for upload",
                "action": "provide 'files' as a list of file paths",
            },
        )
    result = await ctx.upload(body.index, body.files)
    # Backfill the renderer-only fields so the text path can format ``uploaded
    # 2 files to [7]`` without inspecting the original request again.
    result.setdefault("uploaded", len(body.files))
    result.setdefault("index", body.index)
    if wants_text(request):
        return PlainTextResponse(render_upload_text(result))
    return _ok(result, seq=ctx.seq)


# --- Frame ------------------------------------------------------------------


@router.get("/frame/list", response_model=OkEnvelope[FrameListResponse])
async def handle_frame_list(
    ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    frames = await ctx.frame_list()
    data = [{"name": f.name, "url": f.url, "is_current": f.is_current} for f in frames]
    envelope = {"frames": data, "count": len(data)}
    if wants_text(request):
        return PlainTextResponse(render_frame_list_text(envelope))
    return _ok(envelope, seq=ctx.seq)


@router.post("/frame/focus", response_model=OkEnvelope[FrameFocusResponse])
async def handle_frame_focus(
    body: FrameFocusRequest, ctx: BrowserCtxDep, request: Request
) -> Response | dict[str, Any]:
    result = await ctx.frame_focus(name=body.name, url=body.url, main=body.main)
    # Backfill the renderer hint fields without changing the JSON envelope.
    result.setdefault("name", body.name)
    result.setdefault("url", body.url)
    result.setdefault("main", body.main)
    if wants_text(request):
        return PlainTextResponse(render_frame_focus_text(result))
    return _ok(result, seq=ctx.seq)


# --- Registration -----------------------------------------------------------


def register_routers(app: Any) -> None:
    """Register all routes on the FastAPI app."""
    app.include_router(router)


# --- Test-facing helper re-exports ------------------------------------------
# These three callables live on ``ActionService`` and are re-exported here so
# the route-level unit tests can exercise the parsing logic without going
# through the full daemon stack.

_batch_has_refs = ActionService.has_refs
_resolve_action_refs = ActionService.resolve_refs
_traverse = ActionService.traverse
