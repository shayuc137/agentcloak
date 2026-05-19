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
    DEFAULT_BATCH_SETTLE_TIMEOUT,
    DEFAULT_MAX_RETURN_SIZE,
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
    url: str
    timeout: float = DEFAULT_NAVIGATE_TIMEOUT
    include_snapshot: bool = False
    snapshot_mode: Literal["compact", "accessible"] = "compact"


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
    base64: str
    size: int
    format: str


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
    js: str
    world: Literal["main", "isolated"] = "main"
    max_return_size: int = DEFAULT_MAX_RETURN_SIZE


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

    kind: str
    index: int | None = None
    target: str = ""
    include_snapshot: bool = False
    snapshot_mode: Literal["compact", "accessible"] = "compact"


class ActionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    ok: bool = True
    action: str = ""
    seq: int = 0


class BatchActionRequest(BaseModel):
    actions: list[dict[str, Any]] = Field(default_factory=lambda: [])
    sleep: float = 0.0
    settle_timeout: int = DEFAULT_BATCH_SETTLE_TIMEOUT


class BatchActionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    results: list[dict[str, Any]]
    completed: int
    total: int


# --- Fetch ---


class FetchRequest(BaseModel):
    url: str
    method: str = "GET"
    body: str | None = None
    headers: dict[str, str] | None = None
    timeout: float = DEFAULT_NAVIGATE_TIMEOUT


class FetchResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: int = 0
