"""Performance command (7f) — runtime metrics via CDP ``Performance.getMetrics``.

Reports DOM node count, JS heap size, layout/recalc counts and task durations.
The CDP ``Performance`` domain is enabled lazily on first read.
"""

from __future__ import annotations

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import render_performance_metrics_text

__all__ = ["app"]

app = typer.Typer()


@app.command("metrics")
def metrics() -> None:
    """Show current performance counters (DOM nodes, JS heap, layouts, ...)."""
    dispatch_text_or_json(
        DaemonClient(),
        "GET",
        "/performance/metrics",
        renderer=render_performance_metrics_text,
    )
