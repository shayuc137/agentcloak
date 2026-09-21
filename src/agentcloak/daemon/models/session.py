"""Pydantic models for session management routes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

__all__ = ["SessionCloseRequest", "SessionCloseResponse", "SessionListResponse"]


class SessionListResponse(BaseModel):
    sessions: list[dict[str, Any]] = Field(description="Active + suspended sessions.")


class SessionCloseRequest(BaseModel):
    force: bool = Field(
        False,
        description="Cancel session requests and close without waiting in its queue.",
    )
    session_id: str = Field(
        default="", description="Session to close (empty = caller session)."
    )


class SessionCloseResponse(BaseModel):
    closed: bool = Field(description="True if an active session was closed.")
    session_id: str = Field(description="The session that was requested.")
