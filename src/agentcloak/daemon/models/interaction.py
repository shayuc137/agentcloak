"""Pydantic models for page-interaction routes.

Covers dialogs (status/handle), explicit waits, file uploads, frame
inspection and switching, and cookie export/import.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentcloak.daemon.models._defaults import DEFAULT_ACTION_TIMEOUT

__all__ = [
    "CookieDeleteRequest",
    "CookieDeleteResponse",
    "CookieSetRequest",
    "CookieSetResponse",
    "CookiesClearResponse",
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
    skipped: int = 0


class CookieSetRequest(BaseModel):
    """Set cookies directly or parse them from a Copy-as-cURL string (7a R3).

    Provide either ``cookies`` (a list of cookie objects) or ``curl`` (a
    DevTools "Copy as cURL" command whose ``Cookie:`` header / ``-b`` flag is
    parsed into cookie objects). When both are present they are merged.
    """

    cookies: list[dict[str, Any]] | None = Field(
        None, description="Cookie objects (name/value/domain/path) to set."
    )
    curl: str | None = Field(
        None,
        description="Copy-as-cURL command string; cookies are parsed from it.",
    )


class CookieSetResponse(BaseModel):
    set: int = Field(0, description="Number of cookies set.")


class CookiesClearResponse(BaseModel):
    cleared: bool = Field(description="True when all cookies were removed.")


class CookieDeleteRequest(BaseModel):
    """Delete cookies matching a name (optionally scoped to a domain)."""

    name: str = Field(description="Cookie name to delete.")
    domain: str | None = Field(
        None, description="Restrict deletion to cookies on this domain."
    )


class CookieDeleteResponse(BaseModel):
    deleted: int = Field(0, description="Number of cookies removed.")
    name: str = Field("", description="Cookie name that was targeted.")


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
    index: int | None = Field(
        None,
        description=(
            "Element [N] of the file input. Omit to auto-find hidden "
            "input[type=file] elements (drag-drop uploaders)."
        ),
    )
    files: list[str] = Field(
        description="Absolute file paths the daemon reads and hands to the file input."
    )
    nth: int = Field(
        0,
        description=(
            "When auto-finding (no index), pick the nth file input (0-based)."
        ),
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
