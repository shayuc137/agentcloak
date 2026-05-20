"""Pydantic models for console-capture routes (7a R1).

Console messages and uncaught page errors flow into a ring buffer keyed by a
monotonic ``seq`` (independent of the action counter) so ``GET /console?since=N``
pages through them the same way ``/network`` does. ``DELETE``-style clearing
goes through ``POST /console/clear`` to stay consistent with the rest of the
daemon surface (one verb per path).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = [
    "ConsoleClearResponse",
    "ConsoleEntryModel",
    "ConsoleResponse",
]


class ConsoleEntryModel(BaseModel):
    """A single console message or uncaught exception."""

    seq: int = Field(description="Monotonic per-message sequence number.")
    level: str = Field(description="Console level: log, warn, error, info, or debug.")
    text: str = Field(description="Sanitized message text (ANSI/control stripped).")
    url: str | None = Field(None, description="Source URL the message came from.")
    line: int | None = Field(None, description="Source line number, if known.")
    column: int | None = Field(None, description="Source column number, if known.")
    is_error: bool = Field(
        False, description="True for uncaught page errors (page.on('pageerror'))."
    )
    timestamp: float = Field(description="Unix timestamp when the message arrived.")


class ConsoleResponse(BaseModel):
    """Buffered console messages newer than the requested ``since`` seq."""

    entries: list[ConsoleEntryModel] = Field(
        description="Console messages matching the filters."
    )
    seq: int = Field(
        0, description="Highest console seq seen; pass back as 'since' next time."
    )


class ConsoleClearResponse(BaseModel):
    """Result of emptying the console ring buffer."""

    cleared: bool = Field(description="True when the buffer was emptied.")
