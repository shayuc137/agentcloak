"""Pydantic models for session management routes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

__all__ = ["SessionCloseRequest", "SessionCloseResponse", "SessionListResponse"]


class SessionListResponse(BaseModel):
    sessions: list[dict[str, Any]] = Field(description="Active + suspended sessions.")


class SessionCloseRequest(BaseModel):
    session_id: str = Field(
        default="", description="Session to close (empty = default)."
    )


class SessionCloseResponse(BaseModel):
    closed: bool = Field(description="True if a known session was removed.")
    session_id: str = Field(description="The session that was requested.")
