"""Debugger routes (7b T3) — breakpoints, stepping, inspection, anti-debug.

Thin shells over :attr:`BrowserContextBase.debugger`, the shared
:class:`DebuggerManager` that drives the CDP ``Debugger`` (+ ``DOMDebugger``)
domain. The domain is enabled lazily on first ``/debugger/enable`` (or implicitly
by setting a breakpoint), so a session that never debugs never forces
``Debugger.enable`` on the stealth backend's hot path. ``/debugger/disable``
tears it back down to restore that silence.

State-mutating routes echo the live ``enabled``/``paused`` flags + breakpoint
counts so an agent always knows the session state. The step routes
(``/debugger/step``) park until the next pause and return the fresh call frames;
a step that never re-pauses surfaces a structured timeout. Page actions
(navigate, click, ...) are blocked with ``debugger_paused`` while suspended — the
agent clears it with ``/debugger/resume`` or a step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException

from agentcloak.daemon.dependencies import BrowserCtxDep  # noqa: TC001
from agentcloak.daemon.models import (
    BreakpointListResponse,
    BreakpointRemoveRequest,
    BreakpointSetRequest,
    BreakpointSetResponse,
    DebuggerEvaluateRequest,
    DebuggerEvaluateResponse,
    DebuggerOpResponse,
    DebuggerStateResponse,
    OkEnvelope,
    PausedInfoResponse,
    ScopeVariablesRequest,
    ScopeVariablesResponse,
    ScriptsListResponse,
    ScriptSourceRequest,
    ScriptSourceResponse,
    SearchRequest,
    SearchResponse,
    SkipPausesRequest,
    StepRequest,
    XhrBreakpointRequest,
)
from agentcloak.daemon.routes._helpers import _ok

if TYPE_CHECKING:
    from agentcloak.browser.managers import DebuggerManager

__all__ = ["router"]

router = APIRouter()

_STEP_METHODS = {"over", "into", "out"}


def _state(mgr: DebuggerManager) -> dict[str, Any]:
    """Snapshot the debugger's live flags + counts for state responses."""
    return {
        "enabled": mgr.is_enabled,
        "paused": mgr.is_paused,
        "breakpoint_count": len(mgr.list_breakpoints()),
        "xhr_breakpoint_count": len(mgr.list_xhr_breakpoints()),
    }


# --- Lifecycle --------------------------------------------------------------


@router.post("/debugger/enable", response_model=OkEnvelope[DebuggerStateResponse])
async def handle_debugger_enable(ctx: BrowserCtxDep) -> dict[str, Any]:
    mgr = ctx.debugger
    await mgr.enable()
    return _ok(_state(mgr), seq=ctx.seq)


@router.post("/debugger/disable", response_model=OkEnvelope[DebuggerStateResponse])
async def handle_debugger_disable(ctx: BrowserCtxDep) -> dict[str, Any]:
    mgr = ctx.debugger
    await mgr.disable()
    return _ok(_state(mgr), seq=ctx.seq)


# --- Breakpoints ------------------------------------------------------------


@router.post(
    "/debugger/breakpoint/set", response_model=OkEnvelope[BreakpointSetResponse]
)
async def handle_breakpoint_set(
    body: BreakpointSetRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    mgr = ctx.debugger
    info = await mgr.set_breakpoint(body.url, body.line, body.condition)
    return _ok(
        {
            "breakpoint": info.to_dict(),
            "enabled": mgr.is_enabled,
            "breakpoint_count": len(mgr.list_breakpoints()),
        },
        seq=ctx.seq,
    )


@router.post(
    "/debugger/breakpoint/remove", response_model=OkEnvelope[DebuggerOpResponse]
)
async def handle_breakpoint_remove(
    body: BreakpointRemoveRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    mgr = ctx.debugger
    removed = await mgr.remove_breakpoint(body.breakpoint_id)
    return _ok({**_state(mgr), "removed": removed}, seq=ctx.seq)


@router.get(
    "/debugger/breakpoint/list", response_model=OkEnvelope[BreakpointListResponse]
)
async def handle_breakpoint_list(ctx: BrowserCtxDep) -> dict[str, Any]:
    mgr = ctx.debugger
    bps = [b.to_dict() for b in mgr.list_breakpoints()]
    return _ok(
        {
            "breakpoints": bps,
            "xhr_patterns": mgr.list_xhr_breakpoints(),
            "count": len(bps),
        },
        seq=ctx.seq,
    )


@router.post(
    "/debugger/xhr-breakpoint/set", response_model=OkEnvelope[DebuggerStateResponse]
)
async def handle_xhr_breakpoint_set(
    body: XhrBreakpointRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    mgr = ctx.debugger
    await mgr.set_xhr_breakpoint(body.url_pattern)
    return _ok(_state(mgr), seq=ctx.seq)


@router.post(
    "/debugger/xhr-breakpoint/remove", response_model=OkEnvelope[DebuggerOpResponse]
)
async def handle_xhr_breakpoint_remove(
    body: XhrBreakpointRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    mgr = ctx.debugger
    removed = await mgr.remove_xhr_breakpoint(body.url_pattern)
    return _ok({**_state(mgr), "removed": removed}, seq=ctx.seq)


# --- Execution control ------------------------------------------------------


@router.post("/debugger/resume", response_model=OkEnvelope[DebuggerStateResponse])
async def handle_debugger_resume(ctx: BrowserCtxDep) -> dict[str, Any]:
    mgr = ctx.debugger
    await mgr.resume()
    return _ok(_state(mgr), seq=ctx.seq)


@router.post("/debugger/step", response_model=OkEnvelope[PausedInfoResponse])
async def handle_debugger_step(body: StepRequest, ctx: BrowserCtxDep) -> dict[str, Any]:
    if body.type not in _STEP_METHODS:
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "error": "invalid_step_type",
                "hint": f"Unknown step type '{body.type}'",
                "action": f"use one of: {', '.join(sorted(_STEP_METHODS))}",
            },
        )
    mgr = ctx.debugger
    if not mgr.is_paused:
        raise HTTPException(
            status_code=409,
            detail={
                "ok": False,
                "error": "not_paused",
                "hint": "Cannot step — execution is not paused at a breakpoint",
                "action": "set a breakpoint and trigger it before stepping",
            },
        )
    step = {"over": mgr.step_over, "into": mgr.step_into, "out": mgr.step_out}[
        body.type
    ]
    try:
        state = await step()
    except TimeoutError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "ok": False,
                "error": "step_timeout",
                "hint": "Stepped, but execution did not pause again in time",
                "action": "the program may have run to completion; check"
                " '/debugger/paused-info'",
            },
        ) from exc
    return _ok(
        {
            "paused": mgr.is_paused,
            "reason": state.reason,
            "hit_breakpoints": state.hit_breakpoints,
            "call_frames": state.call_frames,
        },
        seq=ctx.seq,
    )


# --- Inspection -------------------------------------------------------------


@router.get("/debugger/paused-info", response_model=OkEnvelope[PausedInfoResponse])
async def handle_debugger_paused_info(ctx: BrowserCtxDep) -> dict[str, Any]:
    mgr = ctx.debugger
    state = mgr.get_paused_info()
    if state is None:
        return _ok(
            {
                "paused": False,
                "reason": "",
                "hit_breakpoints": [],
                "call_frames": [],
            },
            seq=ctx.seq,
        )
    return _ok(
        {
            "paused": True,
            "reason": state.reason,
            "hit_breakpoints": state.hit_breakpoints,
            "call_frames": state.call_frames,
        },
        seq=ctx.seq,
    )


@router.post(
    "/debugger/scope-variables", response_model=OkEnvelope[ScopeVariablesResponse]
)
async def handle_debugger_scope_variables(
    body: ScopeVariablesRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    variables = await ctx.debugger.get_scope_variables(body.object_id)
    return _ok({"variables": variables, "count": len(variables)}, seq=ctx.seq)


@router.post("/debugger/evaluate", response_model=OkEnvelope[DebuggerEvaluateResponse])
async def handle_debugger_evaluate(
    body: DebuggerEvaluateRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    result = await ctx.debugger.evaluate_on_frame(body.call_frame_id, body.expression)
    return _ok(
        {
            "result": result.get("result", {}),
            "exception": result.get("exceptionDetails"),
        },
        seq=ctx.seq,
    )


# --- Scripts ----------------------------------------------------------------


@router.get("/debugger/scripts", response_model=OkEnvelope[ScriptsListResponse])
async def handle_debugger_scripts(ctx: BrowserCtxDep) -> dict[str, Any]:
    scripts = [s.to_dict() for s in ctx.debugger.list_scripts()]
    return _ok({"scripts": scripts, "count": len(scripts)}, seq=ctx.seq)


@router.post("/debugger/script-source", response_model=OkEnvelope[ScriptSourceResponse])
async def handle_debugger_script_source(
    body: ScriptSourceRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    source = await ctx.debugger.get_script_source(body.script_id)
    return _ok({"script_id": body.script_id, "source": source}, seq=ctx.seq)


@router.post("/debugger/search", response_model=OkEnvelope[SearchResponse])
async def handle_debugger_search(
    body: SearchRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    matches = await ctx.debugger.search_in_content(
        body.script_id,
        body.query,
        is_regex=body.is_regex,
        case_sensitive=body.case_sensitive,
    )
    return _ok({"matches": matches, "count": len(matches)}, seq=ctx.seq)


# --- Anti-debug -------------------------------------------------------------


@router.post("/debugger/skip-pauses", response_model=OkEnvelope[DebuggerStateResponse])
async def handle_debugger_skip_pauses(
    body: SkipPausesRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    mgr = ctx.debugger
    await mgr.skip_all_pauses(body.skip)
    return _ok(_state(mgr), seq=ctx.seq)
