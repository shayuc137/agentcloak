"""Pydantic models for the local file-server routes (7a R7).

``cloak serve <dir>`` starts an embedded static HTTP server so an agent can
load local files over http:// (the security layer blocks ``file://``). The
server binds to localhost only and is stopped when the daemon shuts down.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = [
    "ServeStartRequest",
    "ServeStatusResponse",
    "ServeStopResponse",
]


class ServeStartRequest(BaseModel):
    """Start a static file server rooted at ``directory``."""

    directory: str = Field(description="Local directory to serve over http://.")
    port: int | None = Field(
        None,
        description="Preferred port. Omit to auto-allocate a free one.",
    )


class ServeStatusResponse(BaseModel):
    """Current file-server state."""

    running: bool = Field(description="Whether a file server is currently up.")
    directory: str | None = Field(None, description="Served directory, if running.")
    port: int | None = Field(None, description="Bound port, if running.")
    url: str | None = Field(None, description="Base URL agents can navigate to.")


class ServeStopResponse(BaseModel):
    """Result of stopping the file server."""

    stopped: bool = Field(description="True when a running server was stopped.")
