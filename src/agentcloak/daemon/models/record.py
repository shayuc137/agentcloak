"""Session screencast request and artifact metadata."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RecordStartRequest(BaseModel):
    format: Literal["webm", "zip"] = "webm"
    max_frames: int = Field(600, ge=1, le=3000)
    max_seconds: int = Field(120, ge=1, le=600)


class RecordResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    recording: bool
    frames: int = 0
    max_frames: int = 0
    max_seconds: int = 0
    max_bytes: int = 0
    format: str = ""
    tab_id: int | None = None
    size: int = 0
    duration_ms: int = 0
    reason: str = ""
    base64: str = ""
