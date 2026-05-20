"""Profiler commands (7f) — JS coverage, CPU profiling, heap snapshot.

Reverse-engineering aids built on the CDP ``Profiler`` / ``HeapProfiler``
domains (lazily enabled on first use):

* ``coverage-start`` / trigger an action / ``coverage-get`` — see which JS
  functions actually ran, narrowing the hunt for crypto/signing code.
* ``cpu-start`` / trigger an action / ``cpu-stop`` — rank functions by CPU self
  time (the expensive ones are usually the crypto).
* ``heap-snapshot`` — freeze the object graph to a ``.heapsnapshot`` file an
  agent can grep for keys / tokens / decrypted plaintext.

The daemon writes ``cpu-stop --output`` / ``heap-snapshot --output`` files
itself (server-side path), so these commands just print the resulting path.
"""

from __future__ import annotations

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import (
    render_coverage_get_text,
    render_cpu_profile_text,
    render_heap_snapshot_text,
    render_profiler_op_text,
)

__all__ = ["app"]

app = typer.Typer()


@app.command("coverage-start")
def coverage_start() -> None:
    """Begin precise JS coverage (per-function call counts)."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/profiler/coverage/start",
        renderer=render_profiler_op_text,
    )


@app.command("coverage-stop")
def coverage_stop() -> None:
    """Stop precise JS coverage collection."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/profiler/coverage/stop",
        renderer=render_profiler_op_text,
    )


@app.command("coverage-get")
def coverage_get(
    script_id: str = typer.Option(
        "", "--script-id", help="Filter to one script (adds per-function detail)."
    ),
) -> None:
    """Take a coverage snapshot — per-script function coverage, most-covered first."""
    params: dict[str, str] = {}
    if script_id:
        params["script_id"] = script_id
    dispatch_text_or_json(
        DaemonClient(),
        "GET",
        "/profiler/coverage/get",
        params=params,
        renderer=render_coverage_get_text,
    )


@app.command("cpu-start")
def cpu_start() -> None:
    """Start CPU sampling."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/profiler/cpu/start",
        renderer=render_profiler_op_text,
    )


@app.command("cpu-stop")
def cpu_stop(
    output: str = typer.Option(
        "", "--output", "-o", help="Write the raw .cpuprofile JSON here (server-side)."
    ),
) -> None:
    """Stop CPU sampling — ranked hottest functions by self time."""
    body: dict[str, str] = {}
    if output:
        body["output_path"] = output
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/profiler/cpu/stop",
        json_body=body,
        renderer=render_cpu_profile_text,
    )


@app.command("heap-snapshot")
def heap_snapshot(
    output: str = typer.Option(
        ..., "--output", "-o", help="Destination .heapsnapshot file (server-side path)."
    ),
) -> None:
    """Capture a V8 heap snapshot to a file (loads in DevTools → Memory)."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/profiler/heap/snapshot",
        json_body={"output_path": output},
        renderer=render_heap_snapshot_text,
    )
