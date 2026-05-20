"""Browser-action routes: navigate, screenshot, snapshot, evaluate, network,
action, action/batch, fetch — the hot path agents touch on every observe→act
loop. Handlers delegate heavy lifting to :mod:`agentcloak.daemon.services`.
"""

from __future__ import annotations

from typing import Any

import orjson
import structlog
from fastapi import APIRouter, Query

# ``screenshot_to_base64`` lives on the abstract base module so daemon code
# stays backend-agnostic (layer isolation: daemon → BrowserContextBase).
from agentcloak.browser.base import screenshot_to_base64
from agentcloak.daemon.dependencies import (  # noqa: TC001
    BrowserCtxDep,
    ConfigDep,
    ResumeWriterDep,
    SnapshotCacheDep,
)
from agentcloak.daemon.models import (
    ActionRequest,
    ActionResponse,
    BatchActionRequest,
    BatchActionResponse,
    EvaluateRequest,
    EvaluateResponse,
    FetchRequest,
    FetchResponse,
    NavigateRequest,
    NavigateResponse,
    NetworkResponse,
    OkEnvelope,
    ScreenshotResponse,
    SnapshotResponse,
)
from agentcloak.daemon.routes._helpers import (
    _attach_optional_snapshot,
    _ok,
    _update_resume,
)
from agentcloak.daemon.services import ActionService, SnapshotService

__all__ = ["router"]

logger = structlog.get_logger()

router = APIRouter()


# --- Navigate ---------------------------------------------------------------


@router.post("/navigate", response_model=OkEnvelope[NavigateResponse])
async def handle_navigate(
    body: NavigateRequest,
    ctx: BrowserCtxDep,
    config: ConfigDep,
    resume_writer: ResumeWriterDep,
) -> dict[str, Any]:
    result = await ctx.navigate(body.url, timeout=body.timeout)
    await _update_resume(
        resume_writer, ctx, action_summary={"kind": "navigate", "url": body.url}
    )

    if body.include_snapshot:
        await _attach_optional_snapshot(
            result,
            ctx,
            snapshot_mode=body.snapshot_mode,
            snapshot_max_nodes=config.browser.snapshot_max_nodes,
        )

    return _ok(result, seq=ctx.seq)


# --- Screenshot -------------------------------------------------------------


@router.get("/screenshot", response_model=OkEnvelope[ScreenshotResponse])
async def handle_screenshot(
    ctx: BrowserCtxDep,
    config: ConfigDep,
    full_page: bool = Query(
        False,
        description="Capture the full scrollable page instead of the viewport.",
    ),
    format: str = Query(
        "jpeg",
        description="Format: jpeg (smaller) or png (lossless, better for OCR/design).",
    ),
    quality: int | None = Query(
        None,
        description="JPEG quality 1-100; unset uses the default. Ignored for png.",
    ),
) -> dict[str, Any]:
    # ``quality=None`` resolves to the configured default. CLI callers leave
    # this unset and inherit the file/env default; MCP tools pass an explicit
    # lower value so screenshots stay under MCP token budgets.
    if quality is None:
        quality = config.browser.screenshot_quality
    raw = await ctx.screenshot(full_page=full_page, format=format, quality=quality)
    b64 = screenshot_to_base64(raw)
    data = {"base64": b64, "size": len(raw), "format": format}
    return _ok(data, seq=ctx.seq)


# --- Snapshot ---------------------------------------------------------------


@router.get("/snapshot", response_model=OkEnvelope[SnapshotResponse])
async def handle_snapshot(
    ctx: BrowserCtxDep,
    config: ConfigDep,
    snapshot_cache: SnapshotCacheDep,
    mode: str = Query(
        "compact",
        description="Density: compact (token-lean), accessible (full ARIA), or raw.",
    ),
    max_nodes: int = Query(
        -1,
        description="Node cap. -1 auto-caps compact; 0 full tree; >0 explicit cap.",
    ),
    max_chars: int = Query(
        0, description="Character budget for the tree text; 0 means no character cap."
    ),
    focus: int = Query(
        0,
        description="Element [N] to expand into; shows subtree with breadcrumb.",
    ),
    offset: int = Query(0, description="Node offset for paging through a large tree."),
    include_selector_map: bool = Query(
        False, description="Include the [N] → element selector map in the response."
    ),
    frames: bool = Query(
        False, description="Merge child iframe accessibility trees into the snapshot."
    ),
    diff: bool = Query(
        False,
        description="Mark [+] added / [~] changed nodes versus the previous snapshot.",
    ),
) -> dict[str, Any]:
    # ``-1`` = unspecified → cap compact mode at config.browser.snapshot_max_nodes
    # (busy pages exceed agent budgets); ``0`` = explicit full tree; ``>0``
    # = explicit cap. Non-compact modes are always full-payload asks.
    if max_nodes == -1:
        effective_max_nodes = (
            config.browser.snapshot_max_nodes if mode == "compact" else 0
        )
    else:
        effective_max_nodes = max_nodes

    data, cur_cache = await SnapshotService().get(
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

    # ``seq`` only goes in the envelope — CLI/MCP renderers reconstruct
    # the seq for the header line by promoting envelope.seq → data.seq
    # at render time, keeping the daemon JSON shape identical to before.
    return _ok(data, seq=ctx.seq)


# --- Evaluate ---------------------------------------------------------------


@router.post("/evaluate", response_model=OkEnvelope[EvaluateResponse])
async def handle_evaluate(body: EvaluateRequest, ctx: BrowserCtxDep) -> dict[str, Any]:
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
        return _ok(data, seq=ctx.seq)

    data = {"result": result, "truncated": False, "total_size": total_size}
    return _ok(data, seq=ctx.seq)


# --- Network ----------------------------------------------------------------


@router.get("/network", response_model=OkEnvelope[NetworkResponse])
async def handle_network(
    ctx: BrowserCtxDep,
    since: str = Query(
        "0",
        description="Requests after this seq, or 'last_action' for the last action.",
    ),
) -> dict[str, Any]:
    since_value: int | str = int(since) if since.isdigit() else since
    reqs = await ctx.network(since=since_value)
    data = {"requests": reqs, "count": len(reqs)}
    return _ok(data, seq=ctx.seq)


# --- Action -----------------------------------------------------------------


@router.post("/action", response_model=OkEnvelope[ActionResponse])
async def handle_action(
    body: ActionRequest,
    ctx: BrowserCtxDep,
    config: ConfigDep,
    resume_writer: ResumeWriterDep,
) -> dict[str, Any]:
    target = str(body.index) if body.index is not None else body.target
    extra = body.model_dump(exclude_unset=True)
    for known in ("kind", "index", "target", "include_snapshot", "snapshot_mode"):
        extra.pop(known, None)

    # DialogBlockedError from ctx.action() bubbles up to the FastAPI handler
    # (409 with dialog metadata) — no special case needed here.
    result, retried = await ActionService().execute(ctx, body.kind, target, extra=extra)
    if retried:
        result["retried"] = True

    summary = ActionService.build_resume_summary(body.kind, target, extra)
    await _update_resume(resume_writer, ctx, action_summary=summary)

    if body.include_snapshot:
        # On a navigation, ``_post_action_cleanup`` only waited 2s for DOM
        # ready (timeout suppressed). Force a focused wait so the snapshot
        # reflects the new page, not the in-flight loader.
        if result.get("caused_navigation"):
            try:
                await ctx.wait(
                    condition="load", value="domcontentloaded", timeout=10000
                )
            except Exception:
                logger.debug("post_navigation_wait_failed", exc_info=True)
        await _attach_optional_snapshot(
            result,
            ctx,
            snapshot_mode=body.snapshot_mode,
            snapshot_max_nodes=config.browser.snapshot_max_nodes,
        )

    return _ok(result, seq=ctx.seq)


@router.post("/action/batch", response_model=OkEnvelope[BatchActionResponse])
async def handle_action_batch(
    body: BatchActionRequest,
    ctx: BrowserCtxDep,
    config: ConfigDep,
) -> dict[str, Any]:
    settle_timeout = body.settle_timeout or config.browser.batch_settle_timeout
    result = await ActionService().execute_batch(
        ctx, body.actions, sleep_s=body.sleep, settle_timeout=settle_timeout
    )
    return _ok(result, seq=ctx.seq)


# --- Fetch ------------------------------------------------------------------


@router.post("/fetch", response_model=OkEnvelope[FetchResponse])
async def handle_fetch(body: FetchRequest, ctx: BrowserCtxDep) -> dict[str, Any]:
    result = await ctx.fetch(
        body.url,
        method=body.method,
        body=body.body,
        headers=body.headers,
        timeout=body.timeout,
    )
    return _ok(result, seq=ctx.seq)
