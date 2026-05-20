"""Pydantic models for profiler routes (7f) — coverage, CPU, heap snapshot.

All three sit on the CDP ``Profiler`` / ``HeapProfiler`` domains and are
reverse-engineering aids: precise coverage points at the JS that actually ran
(narrowing the search for crypto/signing code), the CPU profile ranks functions
by self time (the expensive ones are usually the crypto), and a heap snapshot
freezes the object graph so an agent can grep it for keys / tokens / decrypted
plaintext. The domain is enabled lazily on the first ``start`` so a session that
never profiles never forces ``Profiler.enable`` on the stealth backend.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = [
    "CoverageFunctionModel",
    "CoverageGetResponse",
    "CoverageScriptModel",
    "CpuProfileResponse",
    "CpuStopRequest",
    "HeapSnapshotRequest",
    "HeapSnapshotResponse",
    "ProfilerOpResponse",
]


class ProfilerOpResponse(BaseModel):
    """A profiler lifecycle op (coverage start/stop, cpu start) acknowledgement."""

    started: bool = Field(
        False, description="True after a successful start of the named profiler."
    )
    stopped: bool = Field(
        False, description="True after a successful stop of the named profiler."
    )


class CoverageFunctionModel(BaseModel):
    """Coverage for a single function within a script."""

    function_name: str = Field(description="Function name (blank for anonymous).")
    covered: bool = Field(
        description="True when at least one of the function's ranges executed."
    )
    call_count: int = Field(
        description="Max execution count across the function's ranges."
    )


class CoverageScriptModel(BaseModel):
    """Per-script coverage summary, ordered most-covered first."""

    script_id: str = Field(description="CDP script id.")
    url: str = Field(description="Script URL (blank for inline / eval'd scripts).")
    functions_total: int = Field(description="Number of functions in the script.")
    functions_covered: int = Field(
        description="Number of functions that executed at least once."
    )
    coverage_pct: float = Field(
        description="functions_covered / functions_total as a 0-100 percentage."
    )
    functions: list[CoverageFunctionModel] = Field(
        default_factory=list["CoverageFunctionModel"],
        description="Per-function detail (only when a single script is requested).",
    )


class CoverageGetResponse(BaseModel):
    """Precise-coverage snapshot taken via ``Profiler.takePreciseCoverage``."""

    scripts: list[CoverageScriptModel] = Field(
        description="Per-script coverage, sorted by coverage percentage descending."
    )
    script_count: int = Field(description="Number of scripts with coverage data.")
    functions_total: int = Field(description="Total functions across all scripts.")
    functions_covered: int = Field(
        description="Total functions that executed at least once."
    )


class CpuStopRequest(BaseModel):
    """Options for stopping a CPU profile."""

    output_path: str = Field(
        "",
        description="Write the raw .cpuprofile JSON here. Empty = return inline.",
    )


class CpuProfileResponse(BaseModel):
    """CPU profile captured between ``Profiler.start`` and ``Profiler.stop``.

    Carries a ranked summary of the hottest functions by self time plus the raw
    profile (node tree + samples) so the agent can do its own analysis. When an
    ``output_path`` was given the raw profile is written to that file instead and
    only ``path`` + the summary come back.
    """

    top_functions: list[dict[str, object]] = Field(
        default_factory=list["dict[str, object]"],
        description="Hottest functions by self hit-count: name, url, line, hits.",
    )
    sample_count: int = Field(0, description="Number of CPU samples collected.")
    duration_us: int = Field(0, description="Profile wall-clock duration (µs).")
    path: str = Field("", description="File the raw profile was written to, if any.")
    profile: dict[str, object] | None = Field(
        None,
        description="Raw CDP profile (omitted when written to output_path).",
    )


class HeapSnapshotRequest(BaseModel):
    """Take a V8 heap snapshot and write it to ``output_path``.

    The snapshot streams back from Chrome as many ``addHeapSnapshotChunk``
    events; the daemon concatenates them and writes the ``.heapsnapshot`` JSON
    to disk (they're large, so we never base64 them over the wire). The file
    loads directly in Chrome DevTools → Memory.
    """

    output_path: str = Field(
        description="Destination file for the .heapsnapshot JSON (required)."
    )


class HeapSnapshotResponse(BaseModel):
    """Result of a heap-snapshot capture."""

    path: str = Field(description="File the snapshot JSON was written to.")
    size: int = Field(description="Snapshot size in bytes.")
