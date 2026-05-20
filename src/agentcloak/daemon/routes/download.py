"""Download routes (7a R2) — direct-URL download, click-triggered wait, listing.

Direct-URL download fetches the URL server-side with the browser's cookies;
the SSRF guard (``core.ssrf_guard.validate_download_url``) runs inside
:meth:`BrowserContextBase.download_url` before any request, so a prompt-injected
agent can't be steered into fetching cloud-metadata / loopback hosts. The wait
variant blocks for the next click-triggered download (Playwright
``page.on('download')`` / CDP ``Page.downloadWillBegin``). Files land on the
daemon host, defaulting to the system temp dir.
"""

from __future__ import annotations

from tempfile import gettempdir
from typing import Any

from fastapi import APIRouter

from agentcloak.daemon.dependencies import BrowserCtxDep  # noqa: TC001
from agentcloak.daemon.models import (
    DownloadListResponse,
    DownloadResponse,
    DownloadUrlRequest,
    DownloadWaitRequest,
    OkEnvelope,
)
from agentcloak.daemon.routes._helpers import _ok

__all__ = ["router"]

router = APIRouter()


@router.post("/download/url", response_model=OkEnvelope[DownloadResponse])
async def handle_download_url(
    body: DownloadUrlRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    # SecurityError from the SSRF guard bubbles up to the global handler as a
    # structured ``download_target_blocked`` / ``download_scheme_blocked``
    # envelope — no special case needed here.
    output_dir = body.output_dir or gettempdir()
    result = await ctx.download_url(body.url, output_dir=output_dir)
    return _ok(result, seq=ctx.seq)


@router.post("/download/wait", response_model=OkEnvelope[DownloadResponse])
async def handle_download_wait(
    body: DownloadWaitRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    output_dir = body.output_dir or gettempdir()
    result = await ctx.download_wait(output_dir=output_dir, timeout=body.timeout)
    return _ok(result, seq=ctx.seq)


@router.get("/download/list", response_model=OkEnvelope[DownloadListResponse])
async def handle_download_list(ctx: BrowserCtxDep) -> dict[str, Any]:
    result = await ctx.download_list()
    return _ok(result, seq=ctx.seq)
