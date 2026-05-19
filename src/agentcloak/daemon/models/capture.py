"""Pydantic models for capture-related routes.

Capture covers traffic recording (start/stop/status/clear), export to HAR,
pattern analysis, and replay. ``CaptureClearResponse`` is intentionally
separate from ``CaptureStatusResponse`` — see its docstring for the history
behind that split.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

__all__ = [
    "CaptureAnalyzeResponse",
    "CaptureClearResponse",
    "CaptureExportResponse",
    "CaptureReplayRequest",
    "CaptureReplayResponse",
    "CaptureStatusResponse",
]


class CaptureStatusResponse(BaseModel):
    recording: bool
    entries: int = 0


class CaptureClearResponse(BaseModel):
    """Result of clearing the capture buffer.

    Distinct from :class:`CaptureStatusResponse` because ``status``/``start``/
    ``stop`` advertise the recording state, while ``clear`` reports whether
    the buffer was emptied. Conflating them led to FastAPI's response-model
    validator rejecting ``/capture/clear`` payloads that omit ``recording``.

    ``entries`` echoes how many records were dropped so agents in text mode
    see e.g. ``cleared 42 entries`` instead of a bare ``cleared``.
    """

    cleared: bool
    entries: int = 0


class CaptureExportResponse(BaseModel):
    """HAR or JSON — open-ended, since HAR has its own deep schema."""

    model_config = ConfigDict(extra="allow")


class CaptureAnalyzeResponse(BaseModel):
    patterns: list[dict[str, Any]]
    count: int


class CaptureReplayRequest(BaseModel):
    url: str
    method: str = "GET"


class CaptureReplayResponse(BaseModel):
    """Replay result — proxies an HTTP response payload."""

    model_config = ConfigDict(extra="allow")

    status: int | None = None
