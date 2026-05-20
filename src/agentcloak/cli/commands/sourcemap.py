"""SourceMap commands (7b T4) — discovery, parsing, position lookup.

Turns a script's declared ``sourceMapURL`` (collected by the debugger) into a
parsed source map so an agent can reverse-map a compiled ``line:column`` back to
the original source file + position and read the embedded original source. The
map is downloaded with the page's cookies (or decoded inline for ``data:``
URIs) and parsed in pure Python.

Typical flow: ``debugger enable`` → (re)load the page → ``sourcemap list`` (find
the script id) → ``sourcemap lookup <id> <line> <col>`` or ``sourcemap
source-content <id> <path>``.

Subcommands are kept flat (``source-content`` rather than a nested group) so
each maps 1:1 to a daemon route and stays easy to script.
"""

from __future__ import annotations

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import (
    render_sourcemap_get_text,
    render_sourcemap_list_text,
    render_sourcemap_lookup_text,
    render_sourcemap_source_content_text,
    render_sourcemap_sources_text,
)

__all__ = ["app"]

app = typer.Typer()


@app.command("list")
def sourcemap_list() -> None:
    """List parsed scripts that declared a source map (id + URL)."""
    dispatch_text_or_json(
        DaemonClient(),
        "GET",
        "/sourcemap/list",
        renderer=render_sourcemap_list_text,
    )


@app.command("get")
def sourcemap_get(
    script_id: str = typer.Argument(..., help="Script id from 'sourcemap list'."),
) -> None:
    """Download + parse a script's source map; prints a metadata summary."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/sourcemap/get",
        json_body={"script_id": script_id},
        renderer=render_sourcemap_get_text,
    )


@app.command("lookup")
def sourcemap_lookup(
    script_id: str = typer.Argument(..., help="Script id from 'sourcemap list'."),
    line: int = typer.Option(
        ..., "--line", help="Zero-based generated (compiled) line number."
    ),
    column: int = typer.Option(
        0, "--column", help="Zero-based generated column number."
    ),
) -> None:
    """Reverse-map a generated line:column to its original source position."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/sourcemap/lookup",
        json_body={"script_id": script_id, "line": line, "column": column},
        renderer=render_sourcemap_lookup_text,
    )


@app.command("sources")
def sourcemap_sources(
    script_id: str = typer.Argument(..., help="Script id from 'sourcemap list'."),
) -> None:
    """List the original source file paths in a script's map."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/sourcemap/sources",
        json_body={"script_id": script_id},
        renderer=render_sourcemap_sources_text,
    )


@app.command("source-content")
def sourcemap_source_content(
    script_id: str = typer.Argument(..., help="Script id from 'sourcemap list'."),
    source_path: str = typer.Argument(
        ..., help="A source path from 'sourcemap sources'."
    ),
) -> None:
    """Print the embedded original source text for one source file."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/sourcemap/source-content",
        json_body={"script_id": script_id, "source_path": source_path},
        renderer=render_sourcemap_source_content_text,
    )
