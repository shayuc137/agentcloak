"""Profiler tool (7f) — JS coverage, CPU profiling, heap snapshot.

One ``agentcloak_profiler`` tool branches on ``action`` to cover the CDP
``Profiler`` / ``HeapProfiler`` surface (mirrors the script/route/streaming
single-tool pattern). Read-only-ish, but profiling toggles session state, so it
is not annotated read-only.
"""

# pyright: reportUnusedFunction=false
# Tools register via @mcp.tool decorator side-effect.

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from agentcloak.core.text_renderers import (
    render_coverage_get_text,
    render_cpu_profile_text,
    render_heap_snapshot_text,
    render_profiler_op_text,
)
from agentcloak.mcp._format import format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool()
    async def agentcloak_profiler(
        action: Literal[
            "coverage_start",
            "coverage_stop",
            "coverage_get",
            "cpu_start",
            "cpu_stop",
            "heap_snapshot",
        ] = "coverage_get",
        script_id: str = "",
        output_path: str = "",
    ) -> str:
        """Profile JS execution to locate crypto/signing code and inspect memory.

        Reverse-engineering workflow built on the CDP Profiler / HeapProfiler
        domains (enabled lazily on first use):

          coverage — coverage_start, trigger an action (e.g. click login),
            then coverage_get to see which functions actually ran. Scripts at
            100% coverage are the code path you just triggered.
          cpu — cpu_start, trigger the action, cpu_stop. The hottest functions
            by self time are usually the crypto/signing.
          heap — heap_snapshot writes the V8 object graph to output_path (a
            .heapsnapshot file); grep it for keys / tokens / decrypted plaintext.

        Actions:
          coverage_start  — begin precise per-function coverage
          coverage_stop   — stop coverage collection
          coverage_get    — snapshot coverage [script_id filters to one script]
          cpu_start       — begin CPU sampling
          cpu_stop        — stop + rank hottest functions [output_path saves raw]
          heap_snapshot   — write a heap snapshot to output_path (required)

        Args:
            action: which profiler operation to run
            script_id: for coverage_get, limit to one script (adds per-function detail)
            output_path: cpu_stop (optional) or heap_snapshot (required) save path

        Returns: action-specific text. coverage_get lists per-script coverage
        percentages; cpu_stop ranks functions by sample hit-count; heap_snapshot
        returns the saved file path.
        """
        if action == "coverage_start":
            return await format_call(
                client.profiler_coverage_start(), render_profiler_op_text
            )
        if action == "coverage_stop":
            return await format_call(
                client.profiler_coverage_stop(), render_profiler_op_text
            )
        if action == "cpu_start":
            return await format_call(
                client.profiler_cpu_start(), render_profiler_op_text
            )
        if action == "cpu_stop":
            return await format_call(
                client.profiler_cpu_stop(output_path=output_path),
                render_cpu_profile_text,
            )
        if action == "heap_snapshot":
            return await format_call(
                client.profiler_heap_snapshot(output_path=output_path),
                render_heap_snapshot_text,
            )
        # Default / "coverage_get".
        return await format_call(
            client.profiler_coverage_get(script_id=script_id),
            render_coverage_get_text,
        )
