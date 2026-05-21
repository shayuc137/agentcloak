"""Debugger tool (7b T3) — breakpoints, stepping, call-stack/scope inspection.

One ``agentcloak_debugger`` tool branches on ``action`` to cover the whole CDP
``Debugger`` surface (mirrors the script/route/streaming single-tool pattern).
Not marked read-only: setting breakpoints and toggling skip-pauses mutate the
debug session.
"""

# pyright: reportUnusedFunction=false
# Tools register via @mcp.tool decorator side-effect.

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

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
from agentcloak.mcp._format import format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool()
    async def agentcloak_debugger(
        action: Literal[
            "enable",
            "disable",
            "breakpoint_set",
            "breakpoint_remove",
            "breakpoint_list",
            "xhr_set",
            "xhr_remove",
            "resume",
            "step",
            "paused_info",
            "scope_variables",
            "evaluate",
            "scripts",
            "script_source",
            "search",
            "skip_pauses",
        ] = "paused_info",
        url: str = "",
        line: int = 0,
        condition: str = "",
        breakpoint_id: str = "",
        url_pattern: str = "",
        step_type: Literal["over", "into", "out"] = "over",
        object_id: str = "",
        call_frame_id: str = "",
        expression: str = "",
        script_id: str = "",
        query: str = "",
        is_regex: bool = False,
        case_sensitive: bool = False,
        skip: bool = True,
    ) -> str:
        """Inspect and control paused JavaScript execution via the CDP debugger.

        Reverse-engineering workhorse: set a breakpoint, let the page hit it,
        then read the call stack + scope and step through. The Debugger domain
        enables lazily on the first 'enable' or 'breakpoint_set' (no setup), and
        'disable' restores stealth silence. While paused, page actions (navigate,
        click, ...) return a 'debugger_paused' error — call 'resume' or 'step'.

        Typical flow:
          breakpoint_set(url=<regex>, line=N) → page triggers it →
          paused_info (read callFrames, copy a callFrameId / scope objectId) →
          scope_variables(object_id=...) / evaluate(call_frame_id=..., expression=...) →
          step(step_type='over'|'into'|'out') or resume

        Actions:
          enable / disable        — turn the Debugger domain on/off
          breakpoint_set          — url (regex) + line [+ condition]
          breakpoint_remove       — breakpoint_id
          breakpoint_list         — list URL + XHR breakpoints
          xhr_set / xhr_remove    — url_pattern (substring; empty = all XHRs)
          resume                  — resume from a pause
          step                    — step_type: over | into | out
          paused_info             — current pause snapshot (reason + call stack)
          scope_variables         — object_id from a frame's scopeChain
          evaluate                — call_frame_id + expression
          scripts                 — list parsed scripts (id, url, source-map)
          script_source           — script_id → full source
          search                  — script_id or url + query [+ is_regex]
          skip_pauses             — skip: ignore all breakpoints (anti-anti-debug)

        Returns: action-specific text. paused_info/step list the call stack with
        each frame's callFrameId in brackets; scope_variables shows name = value.
        """
        if action == "enable":
            return await format_call(
                client.debugger_enable(), render_debugger_state_text
            )
        if action == "disable":
            return await format_call(
                client.debugger_disable(), render_debugger_state_text
            )
        if action == "breakpoint_set":
            return await format_call(
                client.debugger_breakpoint_set(url=url, line=line, condition=condition),
                render_breakpoint_set_text,
            )
        if action == "breakpoint_remove":
            return await format_call(
                client.debugger_breakpoint_remove(breakpoint_id=breakpoint_id),
                render_debugger_op_text,
            )
        if action == "breakpoint_list":
            return await format_call(
                client.debugger_breakpoint_list(), render_breakpoint_list_text
            )
        if action == "xhr_set":
            return await format_call(
                client.debugger_xhr_breakpoint_set(url_pattern=url_pattern),
                render_debugger_state_text,
            )
        if action == "xhr_remove":
            return await format_call(
                client.debugger_xhr_breakpoint_remove(url_pattern=url_pattern),
                render_debugger_op_text,
            )
        if action == "resume":
            return await format_call(
                client.debugger_resume(), render_debugger_state_text
            )
        if action == "step":
            return await format_call(
                client.debugger_step(type=step_type), render_paused_info_text
            )
        if action == "scope_variables":
            return await format_call(
                client.debugger_scope_variables(object_id=object_id),
                render_scope_variables_text,
            )
        if action == "evaluate":
            return await format_call(
                client.debugger_evaluate(
                    call_frame_id=call_frame_id, expression=expression
                ),
                render_debugger_evaluate_text,
            )
        if action == "scripts":
            return await format_call(
                client.debugger_scripts(), render_scripts_list_text
            )
        if action == "script_source":
            return await format_call(
                client.debugger_script_source(script_id=script_id),
                render_script_source_text,
            )
        if action == "search":
            return await format_call(
                client.debugger_search(
                    script_id=script_id or None,
                    url=url or None,
                    query=query,
                    is_regex=is_regex,
                    case_sensitive=case_sensitive,
                ),
                render_debugger_search_text,
            )
        if action == "skip_pauses":
            return await format_call(
                client.debugger_skip_pauses(skip=skip), render_debugger_state_text
            )
        # Default / "paused_info".
        return await format_call(client.debugger_paused_info(), render_paused_info_text)
