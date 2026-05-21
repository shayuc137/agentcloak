"""Debugger commands (7b T3) — breakpoints, stepping, inspection, anti-debug.

Drives the CDP ``Debugger`` domain so an agent can set breakpoints, single-step
paused execution, read the call stack + scope variables, evaluate in a paused
frame, and pull script sources. The domain enables lazily on the first
``enable`` (or implicitly when you set a breakpoint), so a session that never
debugs never pays for it. ``disable`` restores stealth silence.

While execution is paused at a breakpoint, page actions (navigate/click/...) are
blocked with ``debugger_paused`` — clear it with ``debugger resume`` or
``debugger step``.

Subcommands are kept flat (``breakpoint-set`` rather than a nested
``breakpoint set`` group) so each maps 1:1 to a daemon route and stays easy to
script.
"""

from __future__ import annotations

from typing import Any

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import (
    render_breakpoint_list_text,
    render_breakpoint_set_text,
    render_debugger_evaluate_text,
    render_debugger_op_text,
    render_debugger_search_text,
    render_debugger_state_text,
    render_paused_info_text,
    render_scope_variables_text,
    render_script_source_text,
    render_scripts_list_text,
)

__all__ = ["app"]

app = typer.Typer()


@app.command("enable")
def debugger_enable() -> None:
    """Enable the CDP Debugger domain (lazy — does nothing until called)."""
    dispatch_text_or_json(
        DaemonClient(), "POST", "/debugger/enable", renderer=render_debugger_state_text
    )


@app.command("disable")
def debugger_disable() -> None:
    """Disable the Debugger domain and clear paused/script state."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/debugger/disable",
        renderer=render_debugger_state_text,
    )


@app.command("breakpoint-set")
def debugger_breakpoint_set(
    url: str = typer.Argument(..., help="URL regex identifying the script."),
    line: int = typer.Argument(..., help="Zero-based line number to break on."),
    condition: str = typer.Option(
        "", "--condition", help="Break only when this JS expression is truthy."
    ),
) -> None:
    """Set a URL breakpoint (returns the breakpoint id for removal)."""
    body: dict[str, object] = {"url": url, "line": line}
    if condition:
        body["condition"] = condition
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/debugger/breakpoint/set",
        json_body=body,
        renderer=render_breakpoint_set_text,
    )


@app.command("breakpoint-remove")
def debugger_breakpoint_remove(
    breakpoint_id: str = typer.Argument(
        ..., help="Breakpoint id from 'breakpoint-set'."
    ),
) -> None:
    """Remove a URL breakpoint by id."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/debugger/breakpoint/remove",
        json_body={"breakpoint_id": breakpoint_id},
        renderer=render_debugger_op_text,
    )


@app.command("breakpoint-list")
def debugger_breakpoint_list() -> None:
    """List active URL and XHR breakpoints."""
    dispatch_text_or_json(
        DaemonClient(),
        "GET",
        "/debugger/breakpoint/list",
        renderer=render_breakpoint_list_text,
    )


@app.command("xhr-set")
def debugger_xhr_set(
    url_pattern: str = typer.Argument(
        "", help="URL substring to break on; omit to break on every XHR/fetch."
    ),
) -> None:
    """Set a XHR breakpoint by URL substring."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/debugger/xhr-breakpoint/set",
        json_body={"url_pattern": url_pattern},
        renderer=render_debugger_state_text,
    )


@app.command("xhr-remove")
def debugger_xhr_remove(
    url_pattern: str = typer.Argument("", help="XHR breakpoint pattern to remove."),
) -> None:
    """Remove a XHR breakpoint by pattern."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/debugger/xhr-breakpoint/remove",
        json_body={"url_pattern": url_pattern},
        renderer=render_debugger_op_text,
    )


@app.command("resume")
def debugger_resume() -> None:
    """Resume execution from a breakpoint pause."""
    dispatch_text_or_json(
        DaemonClient(), "POST", "/debugger/resume", renderer=render_debugger_state_text
    )


@app.command("step")
def debugger_step(
    type: str = typer.Option(
        "over", "--type", help="Step granularity: over, into, or out."
    ),
) -> None:
    """Single-step paused execution; returns the new call stack."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/debugger/step",
        json_body={"type": type},
        renderer=render_paused_info_text,
    )


@app.command("paused-info")
def debugger_paused_info() -> None:
    """Show the current paused snapshot (reason + call stack)."""
    dispatch_text_or_json(
        DaemonClient(),
        "GET",
        "/debugger/paused-info",
        renderer=render_paused_info_text,
    )


@app.command("scope-variables")
def debugger_scope_variables(
    object_id: str = typer.Argument(..., help="objectId from a frame's scopeChain."),
) -> None:
    """Expand a scope object's own properties (name = value per line)."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/debugger/scope-variables",
        json_body={"object_id": object_id},
        renderer=render_scope_variables_text,
    )


@app.command("evaluate")
def debugger_evaluate(
    call_frame_id: str = typer.Argument(..., help="callFrameId from the paused info."),
    expression: str = typer.Argument(..., help="JS expression to evaluate in-frame."),
) -> None:
    """Evaluate an expression in a paused call frame."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/debugger/evaluate",
        json_body={"call_frame_id": call_frame_id, "expression": expression},
        renderer=render_debugger_evaluate_text,
    )


@app.command("scripts")
def debugger_scripts() -> None:
    """List parsed scripts (id, URL, source-map marker)."""
    dispatch_text_or_json(
        DaemonClient(), "GET", "/debugger/scripts", renderer=render_scripts_list_text
    )


@app.command("script-source")
def debugger_script_source(
    script_id: str = typer.Argument(..., help="Script id from 'scripts'."),
) -> None:
    """Print a script's source text."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/debugger/script-source",
        json_body={"script_id": script_id},
        renderer=render_script_source_text,
    )


@app.command("search")
def debugger_search(
    script_id: str | None = typer.Argument(
        None, help="Script id (omit if using --url)."
    ),
    query: str = typer.Argument(..., help="Substring or regex to match."),
    url: str | None = typer.Option(
        None, "--url", help="URL substring to match scripts (alternative to script_id)."
    ),
    is_regex: bool = typer.Option(False, "--regex", help="Treat query as a regex."),
    case_sensitive: bool = typer.Option(
        False, "--case-sensitive", help="Case-sensitive match."
    ),
) -> None:
    """Search script content by id or URL pattern; prints matching lines."""
    if not script_id and not url:
        from agentcloak.cli.output import error

        error("provide script_id argument or --url pattern")
        raise typer.Exit(1)
    body: dict[str, Any] = {
        "query": query,
        "is_regex": is_regex,
        "case_sensitive": case_sensitive,
    }
    if script_id:
        body["script_id"] = script_id
    if url:
        body["url"] = url
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/debugger/search",
        json_body=body,
        renderer=render_debugger_search_text,
    )


@app.command("skip-pauses")
def debugger_skip_pauses(
    skip: bool = typer.Argument(
        True, help="True ignores all breakpoints/`debugger;` (anti-anti-debug)."
    ),
) -> None:
    """Toggle skipping all pauses (defeats anti-debug `debugger;` loops)."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/debugger/skip-pauses",
        json_body={"skip": skip},
        renderer=render_debugger_state_text,
    )
