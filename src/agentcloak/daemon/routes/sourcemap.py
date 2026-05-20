"""Source-map routes (7b T4) — discovery, parsing, position lookup.

Thin shells over :attr:`BrowserContextBase.sourcemap`, the shared
:class:`SourceMapManager`. It mines the debugger's parsed-script inventory for
``sourceMapURL``s, downloads the ``.map`` (via the page's cookies) or decodes an
inline ``data:`` URI, and decodes the VLQ ``mappings`` in pure Python so an
agent can reverse-map a compiled ``line:column`` back to the original source.

All five routes are read-only. ``/sourcemap/list`` reads the debugger inventory
(empty until the debugger is enabled and the page (re)loaded); the other four
parse-and-cache a specific script's map, so the first call for a given script
pays the download+parse cost and the rest are served from cache.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from agentcloak.daemon.dependencies import BrowserCtxDep  # noqa: TC001
from agentcloak.daemon.models import (
    OkEnvelope,
    SourceMapGetRequest,
    SourceMapGetResponse,
    SourceMapListResponse,
    SourceMapLookupRequest,
    SourceMapLookupResponse,
    SourceMapSourceContentRequest,
    SourceMapSourceContentResponse,
    SourceMapSourcesRequest,
    SourceMapSourcesResponse,
)
from agentcloak.daemon.routes._helpers import _ok

__all__ = ["router"]

router = APIRouter()


@router.get("/sourcemap/list", response_model=OkEnvelope[SourceMapListResponse])
async def handle_sourcemap_list(ctx: BrowserCtxDep) -> dict[str, Any]:
    maps = ctx.sourcemap.list_maps()
    return _ok({"maps": maps, "count": len(maps)}, seq=ctx.seq)


@router.post("/sourcemap/get", response_model=OkEnvelope[SourceMapGetResponse])
async def handle_sourcemap_get(
    body: SourceMapGetRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    parsed = await ctx.sourcemap.get_map(body.script_id)
    return _ok(parsed.metadata(), seq=ctx.seq)


@router.post("/sourcemap/lookup", response_model=OkEnvelope[SourceMapLookupResponse])
async def handle_sourcemap_lookup(
    body: SourceMapLookupRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    result = await ctx.sourcemap.lookup(body.script_id, body.line, body.column)
    return _ok(result, seq=ctx.seq)


@router.post("/sourcemap/sources", response_model=OkEnvelope[SourceMapSourcesResponse])
async def handle_sourcemap_sources(
    body: SourceMapSourcesRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    sources = await ctx.sourcemap.list_sources(body.script_id)
    return _ok({"sources": sources, "count": len(sources)}, seq=ctx.seq)


@router.post(
    "/sourcemap/source-content",
    response_model=OkEnvelope[SourceMapSourceContentResponse],
)
async def handle_sourcemap_source_content(
    body: SourceMapSourceContentRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    content = await ctx.sourcemap.get_source_content(body.script_id, body.source_path)
    return _ok(
        {
            "source_path": body.source_path,
            "content": content,
            "available": content is not None,
        },
        seq=ctx.seq,
    )
