"""Performance route (7f) — runtime metrics via CDP ``Performance.getMetrics``.

A thin shell straight over ``ctx._cdp_send`` (no manager needed — it's a single
read). ``Performance.getMetrics`` requires the domain to be enabled first, so we
lazily ``_cdp_enable_domain("Performance")`` (idempotent) before reading. The
metrics include DOM node count, JS heap size, layout/recalc counts and task
durations — handy for spotting heavy pages or confirming an action's cost.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from agentcloak.daemon.dependencies import BrowserCtxDep  # noqa: TC001
from agentcloak.daemon.models import OkEnvelope, PerformanceMetricsResponse
from agentcloak.daemon.routes._helpers import _ok

__all__ = ["router"]

router = APIRouter()


@router.get(
    "/performance/metrics", response_model=OkEnvelope[PerformanceMetricsResponse]
)
async def handle_performance_metrics(ctx: BrowserCtxDep) -> dict[str, Any]:
    await ctx._cdp_enable_domain("Performance")
    raw = await ctx._cdp_send("Performance.getMetrics")
    raw_metrics: list[dict[str, Any]] = raw.get("metrics") or []
    metrics: list[dict[str, Any]] = []
    for m in raw_metrics:
        metrics.append(
            {
                "name": str(m.get("name", "")),
                "value": float(m.get("value", 0)),
            }
        )
    return _ok({"metrics": metrics, "count": len(metrics)}, seq=ctx.seq)
