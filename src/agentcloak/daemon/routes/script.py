"""Init-script routes (7b T1.1) — inject/remove/list init scripts.

Thin shells over :attr:`BrowserContextBase.script_manager`, which wraps CDP
``Page.addScriptToEvaluateOnNewDocument``. Adding accepts raw JS or a named
hook preset; the preset list is surfaced in the error when an unknown name is
passed so the agent can self-correct.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from agentcloak.browser.managers.script_manager import PRESET_TEMPLATES
from agentcloak.daemon.dependencies import BrowserCtxDep  # noqa: TC001
from agentcloak.daemon.models import (
    OkEnvelope,
    ScriptAddRequest,
    ScriptAddResponse,
    ScriptListResponse,
    ScriptRemoveRequest,
    ScriptRemoveResponse,
)
from agentcloak.daemon.routes._helpers import _ok

__all__ = ["router"]

router = APIRouter()

# Init-script sources can be large; truncate in ``list`` so the surface stays
# scannable. The identifier is what an agent needs to remove one.
_SOURCE_PREVIEW = 200


@router.post("/script/add", response_model=OkEnvelope[ScriptAddResponse])
async def handle_script_add(
    body: ScriptAddRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    mgr = ctx.script_manager
    if body.preset:
        if body.preset not in PRESET_TEMPLATES:
            raise HTTPException(
                status_code=400,
                detail={
                    "ok": False,
                    "error": "unknown_preset",
                    "hint": f"No hook preset named '{body.preset}'",
                    "action": f"use one of: {', '.join(sorted(PRESET_TEMPLATES))}",
                },
            )
        identifier = await mgr.add_preset(body.preset)
        return _ok({"identifier": identifier, "preset": body.preset}, seq=ctx.seq)

    if not body.js:
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "error": "missing_script",
                "hint": "Neither 'js' nor 'preset' was provided",
                "action": "pass raw JS in 'js' or a preset name in 'preset'",
            },
        )
    identifier = await mgr.add(body.js)
    return _ok({"identifier": identifier, "preset": None}, seq=ctx.seq)


@router.post("/script/remove", response_model=OkEnvelope[ScriptRemoveResponse])
async def handle_script_remove(
    body: ScriptRemoveRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    removed = await ctx.script_manager.remove(body.identifier)
    return _ok({"removed": removed}, seq=ctx.seq)


@router.get("/script/list", response_model=OkEnvelope[ScriptListResponse])
async def handle_script_list(ctx: BrowserCtxDep) -> dict[str, Any]:
    scripts = ctx.script_manager.list_scripts()
    rows = [
        {"identifier": ident, "source": src[:_SOURCE_PREVIEW]}
        for ident, src in scripts.items()
    ]
    return _ok({"scripts": rows, "count": len(rows)}, seq=ctx.seq)
