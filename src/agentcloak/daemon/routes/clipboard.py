"""Clipboard routes (7a R5) — read and write the system clipboard.

The backend ``_impl`` grants ``clipboard-read`` / ``clipboard-write`` permission
(headless Chromium blocks it by default) before calling
``navigator.clipboard.readText()`` / ``writeText()``. Routes stay thin envelopes
over :meth:`BrowserContextBase.clipboard_read` / ``clipboard_write``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from agentcloak.daemon.dependencies import BrowserCtxDep  # noqa: TC001
from agentcloak.daemon.models import (
    ClipboardReadResponse,
    ClipboardWriteRequest,
    ClipboardWriteResponse,
    OkEnvelope,
)
from agentcloak.daemon.routes._helpers import _ok

__all__ = ["router"]

router = APIRouter()


@router.get("/clipboard/read", response_model=OkEnvelope[ClipboardReadResponse])
async def handle_clipboard_read(ctx: BrowserCtxDep) -> dict[str, Any]:
    result = await ctx.clipboard_read()
    return _ok(result, seq=ctx.seq)


@router.post("/clipboard/write", response_model=OkEnvelope[ClipboardWriteResponse])
async def handle_clipboard_write(
    body: ClipboardWriteRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    result = await ctx.clipboard_write(body.text)
    return _ok(result, seq=ctx.seq)
