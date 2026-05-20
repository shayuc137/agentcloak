"""Pydantic models for page-interaction routes.

Covers dialogs (status/handle), explicit waits, file uploads, frame
inspection and switching, and cookie export/import.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentcloak.daemon.models._defaults import DEFAULT_ACTION_TIMEOUT

__all__ = [
    "CookiesExportRequest",
    "CookiesExportResponse",
    "CookiesImportRequest",
    "CookiesImportResponse",
    "DialogHandleRequest",
    "DialogHandleResponse",
    "DialogStatusResponse",
    "FrameFocusRequest",
    "FrameFocusResponse",
    "FrameListResponse",
    "UploadRequest",
    "UploadResponse",
    "WaitRequest",
    "WaitResponse",
]


# --- Cookies ---


class CookiesExportRequest(BaseModel):
    url: str | None = Field(
        None,
        description="Scope to this URL's cookies; omit to export all.",
    )


class CookiesExportResponse(BaseModel):
    cookies: list[dict[str, Any]]
    count: int = 0


class CookiesImportRequest(BaseModel):
    cookies: list[dict[str, Any]] = Field(
        description="Cookie objects (name/value/domain/path) to inject."
    )


class CookiesImportResponse(BaseModel):
    imported: int


# --- Dialog ---


class DialogStatusResponse(BaseModel):
    pending: bool
    dialog: dict[str, Any] | None = None


class DialogHandleRequest(BaseModel):
    action: str = Field(
        "accept", description="How to resolve the pending dialog: accept or dismiss."
    )
    text: str | None = Field(
        None, description="Text to enter when accepting a prompt() dialog."
    )


class DialogHandleResponse(BaseModel):
    """Dialog-handle result — varies per dialog kind (alert/confirm/prompt)."""

    model_config = ConfigDict(extra="allow")


# --- Wait ---


class WaitRequest(BaseModel):
    condition: str = Field(
        "ms",
        description="What to wait on: selector, url, load, js, or ms (fixed delay).",
    )
    value: str = Field(
        "1000",
        description="Operand: selector, URL pattern, load state, JS expr, or ms.",
    )
    timeout: int = Field(
        DEFAULT_ACTION_TIMEOUT,
        description="Max ms to wait before the condition is considered failed.",
    )
    state: str = Field(
        "visible",
        description="For selector waits: visible, hidden, attached, or detached.",
    )


class WaitResponse(BaseModel):
    """Wait result — open-ended (depends on the wait condition)."""

    model_config = ConfigDict(extra="allow")


# --- Upload ---


class UploadRequest(BaseModel):
    index: int = Field(description="Element [N] of the file input to attach files to.")
    files: list[str] = Field(
        description="Absolute file paths the daemon reads and hands to the file input."
    )


class UploadResponse(BaseModel):
    """Upload result — typically `{uploaded: N}` plus action feedback."""

    model_config = ConfigDict(extra="allow")


# --- Frame ---


class FrameListResponse(BaseModel):
    frames: list[dict[str, Any]]
    count: int


class FrameFocusRequest(BaseModel):
    name: str | None = Field(
        None, description="Focus the frame whose name/id matches this value."
    )
    url: str | None = Field(
        None, description="Focus the frame whose URL contains this substring."
    )
    main: bool = Field(False, description="Return focus to the top-level main frame.")


class FrameFocusResponse(BaseModel):
    """Frame-focus result — surfaces the active frame's identifiers."""

    model_config = ConfigDict(extra="allow")
