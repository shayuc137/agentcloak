"""Pydantic model for the performance route (7f) — runtime metrics.

``GET /performance/metrics`` reads the CDP ``Performance.getMetrics`` snapshot
(DOM node count, JS heap size, layout/recalc counts, task durations, ...). The
``Performance`` domain is enabled lazily on first read and disabled again so it
doesn't keep accumulating counters on the stealth backend's hot path.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["PerformanceMetricModel", "PerformanceMetricsResponse"]


class PerformanceMetricModel(BaseModel):
    """A single named performance counter."""

    name: str = Field(description="Metric name, e.g. 'JSHeapUsedSize' or 'Nodes'.")
    value: float = Field(description="Metric value (units depend on the metric).")


class PerformanceMetricsResponse(BaseModel):
    """All current performance counters from ``Performance.getMetrics``."""

    metrics: list[PerformanceMetricModel] = Field(
        description="Every metric Chrome reported, in CDP order."
    )
    count: int = Field(description="Number of metrics returned.")
