"""Console-capture routes (7a R1) — read buffered console output, clear it.

The browser layer owns a ring buffer fed by ``page.on('console')`` /
``page.on('pageerror')`` (Playwright) or CDP ``Runtime`` events (RemoteBridge).
These routes are thin envelopes over :meth:`BrowserContextBase.console_entries`
and :meth:`console_clear`; clearing goes through ``POST /console/clear`` to keep
one verb per path (consistent with ``/capture/clear``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from agentcloak.daemon.dependencies import BrowserCtxDep  # noqa: TC001
from agentcloak.daemon.models import (
    ConsoleClearResponse,
    ConsoleResponse,
    OkEnvelope,
)
from agentcloak.daemon.routes._helpers import _ok

__all__ = ["router"]

router = APIRouter()


@router.get("/console", response_model=OkEnvelope[ConsoleResponse])
async def handle_console(
    ctx: BrowserCtxDep,
    since: int = Query(
        0, description="Return console messages with seq greater than this value."
    ),
    limit: int = Query(
        0, description="Cap the number of messages returned (most recent kept)."
    ),
    level: str = Query(
        "", description="Filter to one level: log, warn, error, info, or debug."
    ),
) -> dict[str, Any]:
    result = await ctx.console_entries(since=since, limit=limit, level=level or None)
    return _ok(result, seq=ctx.seq)


@router.post("/console/clear", response_model=OkEnvelope[ConsoleClearResponse])
async def handle_console_clear(ctx: BrowserCtxDep) -> dict[str, Any]:
    result = await ctx.console_clear()
    return _ok(result, seq=ctx.seq)
