"""Pydantic models for clipboard routes (7a R5).

Reading the clipboard takes no body (``GET /clipboard/read``); writing carries
the text to set. Permission granting (clipboard-read / clipboard-write) happens
in the backend ``_impl`` before the ``navigator.clipboard`` call.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = [
    "ClipboardReadResponse",
    "ClipboardWriteRequest",
    "ClipboardWriteResponse",
]


class ClipboardWriteRequest(BaseModel):
    """Text to write into the system clipboard."""

    text: str = Field(description="Text to copy to the clipboard.")


class ClipboardReadResponse(BaseModel):
    """Current clipboard text."""

    text: str = Field("", description="Clipboard contents as text.")


class ClipboardWriteResponse(BaseModel):
    """Confirmation of a clipboard write."""

    written: bool = Field(description="True when the write succeeded.")
    length: int = Field(0, description="Number of characters written.")
