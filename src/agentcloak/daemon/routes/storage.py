"""Storage routes (7a R4) — localStorage / sessionStorage CRUD via evaluate.

Storage has no dedicated backend ``_impl``: both Playwright and RemoteBridge
expose ``evaluate()``, so these routes build a JS snippet with
:mod:`agentcloak.core.storage_helpers` and run it in the page. Each mutating
op is its own ``POST`` path (get/set/delete/clear) to keep one verb per path,
matching the rest of the daemon surface.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from agentcloak.core.storage_helpers import (
    build_storage_clear_js,
    build_storage_delete_js,
    build_storage_get_js,
    build_storage_set_js,
    normalize_storage_type,
)
from agentcloak.daemon.dependencies import BrowserCtxDep  # noqa: TC001
from agentcloak.daemon.models import (
    OkEnvelope,
    StorageClearRequest,
    StorageDeleteRequest,
    StorageGetRequest,
    StorageResponse,
    StorageSetRequest,
)
from agentcloak.daemon.routes._helpers import _ok

__all__ = ["router"]

router = APIRouter()


def _validate_type(storage_type: str) -> str:
    """Normalize the storage area or raise a structured 400."""
    try:
        return normalize_storage_type(storage_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "error": "invalid_storage_type",
                "hint": str(exc),
                "action": "use type=local or type=session",
            },
        ) from exc


@router.post("/storage/get", response_model=OkEnvelope[StorageResponse])
async def handle_storage_get(
    body: StorageGetRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    area = _validate_type(body.type)
    js = build_storage_get_js(area, body.key)
    value = await ctx.evaluate(js)
    data = {"type": area, "key": body.key, "value": value}
    return _ok(data, seq=ctx.seq)


@router.post("/storage/set", response_model=OkEnvelope[StorageResponse])
async def handle_storage_set(
    body: StorageSetRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    area = _validate_type(body.type)
    await ctx.evaluate(build_storage_set_js(area, body.key, body.value))
    data = {"type": area, "key": body.key, "set": True}
    return _ok(data, seq=ctx.seq)


@router.post("/storage/delete", response_model=OkEnvelope[StorageResponse])
async def handle_storage_delete(
    body: StorageDeleteRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    area = _validate_type(body.type)
    await ctx.evaluate(build_storage_delete_js(area, body.key))
    data = {"type": area, "key": body.key, "deleted": True}
    return _ok(data, seq=ctx.seq)


@router.post("/storage/clear", response_model=OkEnvelope[StorageResponse])
async def handle_storage_clear(
    body: StorageClearRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    area = _validate_type(body.type)
    await ctx.evaluate(build_storage_clear_js(area))
    data = {"type": area, "key": None, "cleared": True}
    return _ok(data, seq=ctx.seq)
