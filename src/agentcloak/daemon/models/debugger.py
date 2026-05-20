"""Pydantic models for debugger routes (7b T3).

The debugger drives the CDP ``Debugger`` (+ ``DOMDebugger`` for XHR breakpoints)
domain: set breakpoints, single-step paused execution, read the call stack and
scope variables, evaluate in a paused frame, and pull script sources. The domain
is enabled lazily on first ``/debugger/enable`` (or implicitly by setting a
breakpoint) so a session that never debugs never forces ``Debugger.enable`` on
the stealth backend's hot path.

Most state-mutating routes return :class:`DebuggerStateResponse` (the live
enabled/paused flags + counts) so an agent always knows the session state after
an operation. The richer reads — paused info, scope variables, scripts — have
their own shapes below.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "BreakpointListResponse",
    "BreakpointModel",
    "BreakpointRemoveRequest",
    "BreakpointSetRequest",
    "BreakpointSetResponse",
    "DebuggerEvaluateRequest",
    "DebuggerEvaluateResponse",
    "DebuggerOpResponse",
    "DebuggerStateResponse",
    "PausedInfoResponse",
    "ScopeVariablesRequest",
    "ScopeVariablesResponse",
    "ScriptModel",
    "ScriptSourceRequest",
    "ScriptSourceResponse",
    "ScriptsListResponse",
    "SearchRequest",
    "SearchResponse",
    "SkipPausesRequest",
    "StepRequest",
    "XhrBreakpointRequest",
]


# --- Shared state -----------------------------------------------------------


class DebuggerStateResponse(BaseModel):
    """Live debugger session state, returned after most mutations."""

    enabled: bool = Field(description="Whether the Debugger domain is active.")
    paused: bool = Field(description="Whether execution is currently paused.")
    breakpoint_count: int = Field(description="Number of tracked URL breakpoints.")
    xhr_breakpoint_count: int = Field(description="Number of tracked XHR breakpoints.")


class DebuggerOpResponse(BaseModel):
    """Generic op result for remove-style routes."""

    enabled: bool = Field(description="Whether the Debugger domain is active.")
    paused: bool = Field(description="Whether execution is currently paused.")
    removed: bool = Field(description="Whether the targeted item was tracked/removed.")
    breakpoint_count: int = Field(description="Number of tracked URL breakpoints.")
    xhr_breakpoint_count: int = Field(description="Number of tracked XHR breakpoints.")


# --- Breakpoints ------------------------------------------------------------


class BreakpointModel(BaseModel):
    """One tracked URL breakpoint."""

    breakpoint_id: str = Field(description="CDP-assigned breakpoint id.")
    url: str = Field(description="URL regex the breakpoint matches.")
    line: int = Field(description="Zero-based line number.")
    condition: str = Field(description="Optional JS condition (blank = always).")


class BreakpointSetRequest(BaseModel):
    """Set a URL breakpoint (matched as a regex against the script URL)."""

    url: str = Field(description="URL regex identifying the script to break in.")
    line: int = Field(description="Zero-based line number to break on.")
    condition: str = Field(
        "", description="Optional JS expression; break only when it is truthy."
    )


class BreakpointSetResponse(BaseModel):
    """Result of setting a URL breakpoint."""

    breakpoint: BreakpointModel = Field(description="The newly registered breakpoint.")
    enabled: bool = Field(description="Whether the Debugger domain is active.")
    breakpoint_count: int = Field(description="Number of tracked URL breakpoints.")


class BreakpointRemoveRequest(BaseModel):
    """Remove a URL breakpoint by id."""

    breakpoint_id: str = Field(description="The breakpoint id returned by 'set'.")


class BreakpointListResponse(BaseModel):
    """Tracked URL breakpoints."""

    breakpoints: list[BreakpointModel] = Field(description="Active URL breakpoints.")
    xhr_patterns: list[str] = Field(description="Active XHR breakpoint URL patterns.")
    count: int = Field(description="Number of URL breakpoints.")


class XhrBreakpointRequest(BaseModel):
    """Set/remove a XHR breakpoint by URL substring (empty = all XHRs)."""

    url_pattern: str = Field(
        "", description="URL substring to break on; empty matches every XHR/fetch."
    )


# --- Stepping ---------------------------------------------------------------


class StepRequest(BaseModel):
    """Single-step paused execution."""

    type: str = Field("over", description="Step granularity: 'over', 'into', or 'out'.")


# --- Inspection -------------------------------------------------------------


class PausedInfoResponse(BaseModel):
    """Current paused snapshot, or empty when running.

    ``call_frames`` and the scope chain carry CDP remote-object handles
    (``objectId``) — feed a scope object's id to ``/debugger/scope-variables`` to
    expand it, and a frame's ``callFrameId`` to ``/debugger/evaluate``.
    """

    paused: bool = Field(description="Whether execution is currently paused.")
    reason: str = Field(
        "", description="Stop reason (e.g. 'other', 'XHR', 'EventListener')."
    )
    hit_breakpoints: list[str] = Field(
        default_factory=list[str],
        description="Breakpoint ids that triggered the stop.",
    )
    call_frames: list[dict[str, Any]] = Field(
        default_factory=list[dict[str, Any]],
        description="Full CDP call frames (location + scopeChain).",
    )


class ScopeVariablesRequest(BaseModel):
    """Expand a scope/object's own properties."""

    object_id: str = Field(
        description="CDP objectId from a call frame's scopeChain[].object.objectId."
    )


class ScopeVariablesResponse(BaseModel):
    """Own properties of a resolved scope object."""

    variables: list[dict[str, Any]] = Field(
        description="CDP PropertyDescriptor list (name + value remote object)."
    )
    count: int = Field(description="Number of properties returned.")


class DebuggerEvaluateRequest(BaseModel):
    """Evaluate an expression in a paused call frame."""

    call_frame_id: str = Field(
        description="CDP callFrameId from a frame in the paused info."
    )
    expression: str = Field(description="JS expression to evaluate in that frame.")


class DebuggerEvaluateResponse(BaseModel):
    """Result of evaluating in a paused frame."""

    result: dict[str, Any] = Field(
        default_factory=dict, description="CDP remote object for the evaluated value."
    )
    exception: dict[str, Any] | None = Field(
        None, description="CDP exceptionDetails when the expression threw."
    )


# --- Scripts ----------------------------------------------------------------


class ScriptModel(BaseModel):
    """One parsed script."""

    script_id: str = Field(description="CDP script id.")
    url: str = Field(description="Script URL (blank for inline/eval scripts).")
    source_map_url: str = Field(description="Source map URL/data-URI, if declared.")
    start_line: int = Field(description="First line in the containing resource.")
    end_line: int = Field(description="Last line in the containing resource.")
    hash: str = Field(description="CDP content hash.")


class ScriptsListResponse(BaseModel):
    """Parsed scripts seen since the last navigation."""

    scripts: list[ScriptModel] = Field(description="Parsed scripts.")
    count: int = Field(description="Number of scripts.")


class ScriptSourceRequest(BaseModel):
    """Fetch a script's source text by id."""

    script_id: str = Field(description="The script id from '/debugger/scripts'.")


class ScriptSourceResponse(BaseModel):
    """A script's source text."""

    script_id: str = Field(description="The requested script id.")
    source: str = Field(description="Full script source text.")


class SearchRequest(BaseModel):
    """Search within a script's content."""

    script_id: str = Field(description="The script id to search in.")
    query: str = Field(description="Substring (or regex) to match.")
    is_regex: bool = Field(False, description="Treat 'query' as a regex.")
    case_sensitive: bool = Field(False, description="Case-sensitive match.")


class SearchResponse(BaseModel):
    """Matches from a content search."""

    matches: list[dict[str, Any]] = Field(
        description="CDP SearchMatch list (lineNumber + lineContent)."
    )
    count: int = Field(description="Number of matches.")


# --- Anti-debug -------------------------------------------------------------


class SkipPausesRequest(BaseModel):
    """Toggle whether all breakpoints/`debugger;` statements are skipped."""

    skip: bool = Field(description="True ignores every pause (anti-anti-debug).")
