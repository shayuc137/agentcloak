"""Network route-interception routes (7b T1.3) — add/remove/list rules.

Thin shells over :attr:`BrowserContextBase.route_manager`. The shared
:class:`RouteManager` owns matching + the rule list; the active backend owns
the transport (Playwright ``page.route`` vs CDP ``Fetch``). Adding validates
the action up front so a typo fails fast with the valid set.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from agentcloak.browser.managers.route_manager import RouteRule
from agentcloak.daemon.dependencies import BrowserCtxDep  # noqa: TC001
from agentcloak.daemon.models import (
    OkEnvelope,
    RouteAddRequest,
    RouteListResponse,
    RouteOpResponse,
    RouteRemoveRequest,
)
from agentcloak.daemon.routes._helpers import _ok

__all__ = ["router"]

router = APIRouter()

_VALID_ACTIONS = ("abort", "fulfill", "continue")


@router.post("/route/add", response_model=OkEnvelope[RouteOpResponse])
async def handle_route_add(body: RouteAddRequest, ctx: BrowserCtxDep) -> dict[str, Any]:
    if body.action not in _VALID_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "error": "invalid_action",
                "hint": f"Unknown route action '{body.action}'",
                "action": f"use one of: {', '.join(_VALID_ACTIONS)}",
            },
        )
    rule = RouteRule(
        pattern=body.pattern,
        action=body.action,
        resource_type=body.resource_type or None,
        method=body.method or None,
        status=body.status or None,
        content_type=body.content_type or None,
        body=body.body or None,
    )
    mgr = ctx.route_manager
    await mgr.add(rule)
    return _ok(
        {"pattern": body.pattern, "removed": 0, "count": len(mgr.list_rules())},
        seq=ctx.seq,
    )


@router.post("/route/remove", response_model=OkEnvelope[RouteOpResponse])
async def handle_route_remove(
    body: RouteRemoveRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    mgr = ctx.route_manager
    pattern = body.pattern or None
    removed = await mgr.remove(pattern)
    return _ok(
        {"pattern": pattern, "removed": removed, "count": len(mgr.list_rules())},
        seq=ctx.seq,
    )


@router.get("/route/list", response_model=OkEnvelope[RouteListResponse])
async def handle_route_list(ctx: BrowserCtxDep) -> dict[str, Any]:
    rules = [r.to_dict() for r in ctx.route_manager.list_rules()]
    return _ok({"rules": rules, "count": len(rules)}, seq=ctx.seq)
