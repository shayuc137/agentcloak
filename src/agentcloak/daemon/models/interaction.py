"""Pydantic models for page-interaction routes.

Covers dialogs (status/handle), explicit waits, file uploads, frame
inspection and switching, and cookie export/import.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

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
    url: str | None = None


class CookiesExportResponse(BaseModel):
    cookies: list[dict[str, Any]]
    count: int = 0


class CookiesImportRequest(BaseModel):
    cookies: list[dict[str, Any]]


class CookiesImportResponse(BaseModel):
    imported: int


# --- Dialog ---


class DialogStatusResponse(BaseModel):
    pending: bool
    dialog: dict[str, Any] | None = None


class DialogHandleRequest(BaseModel):
    action: str = "accept"
    text: str | None = None


class DialogHandleResponse(BaseModel):
    """Dialog-handle result — varies per dialog kind (alert/confirm/prompt)."""

    model_config = ConfigDict(extra="allow")


# --- Wait ---


class WaitRequest(BaseModel):
    condition: str = "ms"
    value: str = "1000"
    timeout: int = DEFAULT_ACTION_TIMEOUT
    state: str = "visible"


class WaitResponse(BaseModel):
    """Wait result — open-ended (depends on the wait condition)."""

    model_config = ConfigDict(extra="allow")


# --- Upload ---


class UploadRequest(BaseModel):
    index: int
    files: list[str]


class UploadResponse(BaseModel):
    """Upload result — typically `{uploaded: N}` plus action feedback."""

    model_config = ConfigDict(extra="allow")


# --- Frame ---


class FrameListResponse(BaseModel):
    frames: list[dict[str, Any]]
    count: int


class FrameFocusRequest(BaseModel):
    name: str | None = None
    url: str | None = None
    main: bool = False


class FrameFocusResponse(BaseModel):
    """Frame-focus result — surfaces the active frame's identifiers."""

    model_config = ConfigDict(extra="allow")
