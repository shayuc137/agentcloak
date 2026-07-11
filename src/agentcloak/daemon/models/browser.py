"""Pydantic models for browser-facing routes.

Covers navigate, screenshot, snapshot, evaluate, network, action,
action/batch, and fetch. These are the highest-traffic endpoints — the
ones agents touch on every observe→act loop — and they share enough
shape that grouping them together keeps the per-route imports tight.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentcloak.daemon.models._defaults import (
    DEFAULT_NAVIGATE_TIMEOUT,
)

__all__ = [
    "ActionRequest",
    "ActionResponse",
    "BatchActionRequest",
    "BatchActionResponse",
    "EvaluateRequest",
    "EvaluateResponse",
    "FetchRequest",
    "FetchResponse",
    "NavigateRequest",
    "NavigateResponse",
    "NetworkResponse",
    "ScreenshotResponse",
    "SnapshotResponse",
]


# --- Navigate ---


class NavigateRequest(BaseModel):
    url: str = Field(
        description=(
            "Target URL (http/https/about). file/data/javascript schemes blocked."
        )
    )
    timeout: float = Field(
        DEFAULT_NAVIGATE_TIMEOUT,
        description="Navigation timeout in seconds before giving up.",
    )
    include_snapshot: bool = Field(
        False,
        description="Attach a snapshot so you observe+act in one round-trip.",
    )
    snapshot_mode: Literal["compact", "accessible"] = Field(
        "compact",
        description="Snapshot density: compact (token-lean) or accessible (full ARIA).",
    )


class _SnapshotAttachment(BaseModel):
    """Lightweight snapshot payload attached to action/navigate responses."""

    tree_text: str
    mode: str
    total_nodes: int
    total_interactive: int


class NavigateResponse(BaseModel):
    """Navigation result — kept open-ended to allow backend-specific fields."""

    model_config = ConfigDict(extra="allow")

    url: str = ""
    title: str = ""
    status: int | None = None
    snapshot: _SnapshotAttachment | None = None


# --- Screenshot ---


class ScreenshotResponse(BaseModel):
    """Screenshot result.

    Without ``output_path`` the daemon returns ``base64`` for the CLI/MCP to
    decode. With ``output_path`` (7a R8) the daemon writes the image itself
    and returns ``path`` + ``size`` instead, so API/MCP callers driving a
    daemon on the same host get a file without round-tripping base64.
    """

    model_config = ConfigDict(extra="allow")

    base64: str = ""
    size: int = 0
    format: str = ""
    path: str | None = None


# --- Snapshot ---


class SnapshotResponse(BaseModel):
    """Snapshot tree + optional metadata."""

    model_config = ConfigDict(extra="allow")

    url: str
    title: str
    mode: str
    tree_text: str
    tree_size: int
    truncated: bool
    total_nodes: int
    total_interactive: int
    truncated_at: int | None = None
    diff: bool | None = None
    selector_map: dict[str, dict[str, Any]] | None = None
    security_warnings: list[dict[str, Any]] | None = None


# --- Evaluate ---


class EvaluateRequest(BaseModel):
    js: str = Field(
        "",
        description="JavaScript expression or function body to evaluate in the page.",
    )
    world: Literal["main", "isolated"] = Field(
        "main",
        description="World: main (sees site globals) or isolated (sandboxed).",
    )
    max_return_size: int | None = Field(
        None,
        description=(
            "Max serialized result bytes; unset uses browser.max_return_size."
        ),
    )
    preset: str = Field(
        "",
        description=(
            "Reverse-engineering preset to run instead of 'js' (forced to the "
            "main world). One of: vue_inspect, react_inspect, jwt_decode, "
            "cookie_parse, storage_dump."
        ),
    )


class EvaluateResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    result: Any = None
    truncated: bool = False
    total_size: int = 0


# --- Network ---


class NetworkResponse(BaseModel):
    requests: list[dict[str, Any]]
    count: int


# --- Action ---


class ActionRequest(BaseModel):
    """One action invocation. Extra params (text, key, value, etc.) pass through."""

    model_config = ConfigDict(extra="allow")

    kind: str = Field(
        description="Verb: click/fill/type/scroll/hover/select/press/keydown/keyup.",
    )
    index: int | None = Field(
        None,
        description="Element [N] from snapshot. Preferred over target/coordinates.",
    )
    target: str = Field(
        "",
        description="Element selector or 'x,y' coordinate fallback when no index.",
    )
    include_snapshot: bool = Field(
        False,
        description="Attach a snapshot after the action to see the result.",
    )
    snapshot_mode: Literal["compact", "accessible"] = Field(
        "compact",
        description="Snapshot density: compact (token-lean) or accessible (full ARIA).",
    )


class ActionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    ok: bool = True
    action: str = ""
    seq: int = 0


class BatchActionRequest(BaseModel):
    actions: list[dict[str, Any]] = Field(
        default_factory=lambda: [],
        description="Ordered action objects; may reference prior results via $N.path.",
    )
    sleep: float = Field(
        0.0, description="Seconds to pause between actions to let the page settle."
    )
    settle_timeout: int | None = Field(
        None,
        description=(
            "Max ms to wait for navigation/network to settle per action; "
            "unset uses browser.batch_settle_timeout."
        ),
    )


class BatchActionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    results: list[dict[str, Any]]
    completed: int
    total: int


# --- Fetch ---


class FetchRequest(BaseModel):
    url: str = Field(
        description="Target URL fetched with the browser's cookies and TLS fingerprint."
    )
    method: str = Field("GET", description="HTTP method (GET, POST, PUT, DELETE, ...).")
    body: str | None = Field(
        None, description="Request body string for POST/PUT requests."
    )
    headers: dict[str, str] | None = Field(
        None, description="Extra request headers merged on top of the browser defaults."
    )
    timeout: float = Field(
        DEFAULT_NAVIGATE_TIMEOUT, description="Request timeout in seconds."
    )


class FetchResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: int = 0
