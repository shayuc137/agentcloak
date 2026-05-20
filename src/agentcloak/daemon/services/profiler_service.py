"""ProfilerService — coverage / CPU / heap logic for the ``/profiler`` routes (7f).

These capabilities are thin wrappers over the CDP ``Profiler`` and
``HeapProfiler`` domains, but the *shaping* of their results is non-trivial:
precise coverage has to be folded into a per-script percentage summary, the CPU
profile has to be ranked by self time, and the heap snapshot streams back as
many ``addHeapSnapshotChunk`` events that must be collected and written to disk.
Keeping that here lets the route handlers stay thin and lets the tests exercise
the aggregation against a mocked ``ctx`` (its ``_cdp_send`` / ``_on_cdp_event``)
without a real browser.

The ``Profiler`` domain is enabled lazily via ``ctx._cdp_enable_domain`` (which
is idempotent), so a session that never profiles never forces ``Profiler.enable``
on the stealth backend's hot path.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

__all__ = ["ProfilerService"]

# Default number of hottest functions to surface in a CPU-profile summary. The
# raw profile (node tree + samples) is still returned in full so an agent can
# do its own analysis; this is just the at-a-glance ranking.
_TOP_FUNCTIONS = 15

# How long to wait for ``HeapProfiler.addHeapSnapshotChunk`` events to stop
# arriving after ``takeHeapSnapshot`` returns. Chrome emits the chunks as the
# command runs and the ``takeHeapSnapshot`` result resolves once they're all
# dispatched, but we poll briefly for quiescence to be safe against ordering.
_HEAP_CHUNK_QUIET_S = 0.1
_HEAP_CHUNK_MAX_WAIT_S = 30.0


class ProfilerService:
    """Stateless helpers for the profiler routes."""

    # --- Coverage (Profiler.startPreciseCoverage / takePreciseCoverage) ----

    @staticmethod
    async def coverage_start(ctx: Any) -> dict[str, Any]:
        """Begin precise coverage (per-function call counts)."""
        await ctx._cdp_enable_domain("Profiler")
        await ctx._cdp_send(
            "Profiler.startPreciseCoverage",
            {"callCount": True, "detailed": True},
        )
        return {"started": True}

    @staticmethod
    async def coverage_stop(ctx: Any) -> dict[str, Any]:
        """Stop precise coverage collection."""
        await ctx._cdp_send("Profiler.stopPreciseCoverage")
        return {"stopped": True}

    @staticmethod
    async def coverage_get(ctx: Any, *, script_id: str = "") -> dict[str, Any]:
        """Take a precise-coverage snapshot and fold it into a per-script summary.

        ``script_id`` filters to a single script and includes its per-function
        detail; without it, every covered script is summarised (no per-function
        rows, to keep the payload small).
        """
        await ctx._cdp_enable_domain("Profiler")
        raw = await ctx._cdp_send("Profiler.takePreciseCoverage")
        result: list[dict[str, Any]] = list(raw.get("result", []) or [])

        scripts: list[dict[str, Any]] = []
        total_funcs = 0
        total_covered = 0
        for entry in result:
            sid = str(entry.get("scriptId", "") or "")
            if script_id and sid != script_id:
                continue
            functions: list[dict[str, Any]] = list(entry.get("functions", []) or [])
            fn_rows: list[dict[str, Any]] = []
            covered_count = 0
            for fn in functions:
                ranges: list[dict[str, Any]] = list(fn.get("ranges", []) or [])
                max_count = max(
                    (int(r.get("count", 0) or 0) for r in ranges), default=0
                )
                is_covered = max_count > 0
                if is_covered:
                    covered_count += 1
                fn_rows.append(
                    {
                        "function_name": str(fn.get("functionName", "") or ""),
                        "covered": is_covered,
                        "call_count": max_count,
                    }
                )
            fn_total = len(functions)
            total_funcs += fn_total
            total_covered += covered_count
            pct = round((covered_count / fn_total * 100.0), 1) if fn_total else 0.0
            scripts.append(
                {
                    "script_id": sid,
                    "url": str(entry.get("url", "") or ""),
                    "functions_total": fn_total,
                    "functions_covered": covered_count,
                    "coverage_pct": pct,
                    # Only attach per-function detail when a single script was
                    # requested — the full list is huge for vendor bundles.
                    "functions": fn_rows if script_id else [],
                }
            )

        scripts.sort(key=lambda s: s["coverage_pct"], reverse=True)
        return {
            "scripts": scripts,
            "script_count": len(scripts),
            "functions_total": total_funcs,
            "functions_covered": total_covered,
        }

    # --- CPU profile (Profiler.start / stop) -------------------------------

    @staticmethod
    async def cpu_start(ctx: Any) -> dict[str, Any]:
        """Start CPU sampling."""
        await ctx._cdp_enable_domain("Profiler")
        await ctx._cdp_send("Profiler.start")
        return {"started": True}

    @staticmethod
    async def cpu_stop(ctx: Any, *, output_path: str = "") -> dict[str, Any]:
        """Stop CPU sampling and summarise the hottest functions.

        Returns a ranked ``top_functions`` summary always; the raw profile is
        either returned inline or, when ``output_path`` is set, written to disk
        (CPU profiles are large and a `.cpuprofile` file loads in DevTools).
        """
        raw = await ctx._cdp_send("Profiler.stop")
        profile: dict[str, Any] = dict(raw.get("profile", {}) or {})
        top = ProfilerService._rank_cpu_nodes(profile)

        samples: list[Any] = list(profile.get("samples", []) or [])
        start = int(profile.get("startTime", 0) or 0)
        end = int(profile.get("endTime", 0) or 0)
        out: dict[str, Any] = {
            "top_functions": top,
            "sample_count": len(samples),
            "duration_us": max(end - start, 0),
        }

        if output_path:
            import orjson

            dest = Path(output_path).expanduser()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(orjson.dumps(profile))
            out["path"] = str(dest)
            out["profile"] = None
        else:
            out["path"] = ""
            out["profile"] = profile
        return out

    @staticmethod
    def _rank_cpu_nodes(profile: dict[str, Any]) -> list[dict[str, Any]]:
        """Rank profile nodes by self hit-count, hottest first.

        A CDP CPU profile is a node tree where each node carries a ``hitCount``
        (samples that landed directly in that function) and a ``callFrame``. We
        sort by ``hitCount`` and surface the function identity so the agent sees
        which functions burned the most CPU — usually the crypto/signing code.
        """
        nodes: list[dict[str, Any]] = list(profile.get("nodes", []) or [])
        ranked: list[dict[str, Any]] = []
        for node in nodes:
            hits = int(node.get("hitCount", 0) or 0)
            if hits <= 0:
                continue
            frame: dict[str, Any] = dict(node.get("callFrame", {}) or {})
            ranked.append(
                {
                    "name": str(frame.get("functionName", "") or "(anonymous)"),
                    "url": str(frame.get("url", "") or ""),
                    "line": int(frame.get("lineNumber", 0) or 0),
                    "hits": hits,
                }
            )
        ranked.sort(key=lambda n: n["hits"], reverse=True)
        return ranked[:_TOP_FUNCTIONS]

    # --- Heap snapshot (HeapProfiler.takeHeapSnapshot) ---------------------

    @staticmethod
    async def heap_snapshot(ctx: Any, *, output_path: str) -> dict[str, Any]:
        """Capture a V8 heap snapshot and write the JSON to ``output_path``.

        Chrome streams the snapshot back as a sequence of
        ``HeapProfiler.addHeapSnapshotChunk`` events rather than as the command
        result, so we register a collector, enable the domain, fire the command,
        wait for the chunks to drain, then concatenate and write them.
        """
        chunks: list[str] = []

        def _collect(params: dict[str, Any]) -> None:
            chunk = params.get("chunk")
            if isinstance(chunk, str):
                chunks.append(chunk)

        ctx._on_cdp_event("HeapProfiler.addHeapSnapshotChunk", _collect)
        await ctx._cdp_enable_domain("HeapProfiler")
        await ctx._cdp_send("HeapProfiler.takeHeapSnapshot", {"reportProgress": False})

        # The takeHeapSnapshot result resolves after the chunks are dispatched,
        # but poll for quiescence so a late chunk can't be dropped.
        await ProfilerService._wait_for_chunks(chunks)

        dest = Path(output_path).expanduser()
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(chunks).encode("utf-8")
        dest.write_bytes(payload)
        return {"path": str(dest), "size": len(payload)}

    @staticmethod
    async def _wait_for_chunks(chunks: list[str]) -> None:
        """Wait until ``chunks`` stops growing (or the max wait elapses)."""
        waited = 0.0
        last_len = -1
        while waited < _HEAP_CHUNK_MAX_WAIT_S:
            if len(chunks) == last_len and chunks:
                return
            last_len = len(chunks)
            await asyncio.sleep(_HEAP_CHUNK_QUIET_S)
            waited += _HEAP_CHUNK_QUIET_S
