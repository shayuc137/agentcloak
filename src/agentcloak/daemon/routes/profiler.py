"""Profiler routes (7f) — JS coverage, CPU profiling, heap snapshot.

Thin shells over :class:`ProfilerService`, which drives the CDP ``Profiler`` and
``HeapProfiler`` domains. The domain is enabled lazily on the first ``start`` /
``get`` so a session that never profiles never forces ``Profiler.enable`` on the
stealth backend's hot path.

Reverse-engineering use:

* **coverage** — start, trigger an action (e.g. click login), then ``get`` to
  see which JS functions actually ran, narrowing the hunt for crypto/signing.
* **cpu** — start, trigger the action, stop; the hottest functions by self time
  are usually the crypto/signing code.
* **heap** — freeze the object graph and write it to a ``.heapsnapshot`` file an
  agent can grep for keys / tokens / decrypted plaintext.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from agentcloak.daemon.dependencies import BrowserCtxDep  # noqa: TC001
from agentcloak.daemon.models import (
    CoverageGetResponse,
    CpuProfileResponse,
    CpuStopRequest,
    HeapSnapshotRequest,
    HeapSnapshotResponse,
    OkEnvelope,
    ProfilerOpResponse,
)
from agentcloak.daemon.routes._helpers import _ok
from agentcloak.daemon.services import ProfilerService

__all__ = ["router"]

router = APIRouter()


# --- Coverage ---------------------------------------------------------------


@router.post("/profiler/coverage/start", response_model=OkEnvelope[ProfilerOpResponse])
async def handle_coverage_start(ctx: BrowserCtxDep) -> dict[str, Any]:
    data = await ProfilerService.coverage_start(ctx)
    return _ok(data, seq=ctx.seq)


@router.post("/profiler/coverage/stop", response_model=OkEnvelope[ProfilerOpResponse])
async def handle_coverage_stop(ctx: BrowserCtxDep) -> dict[str, Any]:
    data = await ProfilerService.coverage_stop(ctx)
    return _ok(data, seq=ctx.seq)


@router.get("/profiler/coverage/get", response_model=OkEnvelope[CoverageGetResponse])
async def handle_coverage_get(
    ctx: BrowserCtxDep,
    script_id: str = Query(
        "", description="Filter to one script id (includes per-function detail)."
    ),
) -> dict[str, Any]:
    data = await ProfilerService.coverage_get(ctx, script_id=script_id)
    return _ok(data, seq=ctx.seq)


# --- CPU profiling ----------------------------------------------------------


@router.post("/profiler/cpu/start", response_model=OkEnvelope[ProfilerOpResponse])
async def handle_cpu_start(ctx: BrowserCtxDep) -> dict[str, Any]:
    data = await ProfilerService.cpu_start(ctx)
    return _ok(data, seq=ctx.seq)


@router.post("/profiler/cpu/stop", response_model=OkEnvelope[CpuProfileResponse])
async def handle_cpu_stop(body: CpuStopRequest, ctx: BrowserCtxDep) -> dict[str, Any]:
    data = await ProfilerService.cpu_stop(ctx, output_path=body.output_path)
    return _ok(data, seq=ctx.seq)


# --- Heap snapshot ----------------------------------------------------------


@router.post("/profiler/heap/snapshot", response_model=OkEnvelope[HeapSnapshotResponse])
async def handle_heap_snapshot(
    body: HeapSnapshotRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    data = await ProfilerService.heap_snapshot(ctx, output_path=body.output_path)
    return _ok(data, seq=ctx.seq)
