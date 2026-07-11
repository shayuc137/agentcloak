"""Pydantic models for persistent page-overlay hiding."""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = [
    "HideAddRequest",
    "HideAddResponse",
    "HideListResponse",
    "HideRemoveRequest",
    "HideRemoveResponse",
    "HideSelectorModel",
]


class HideSelectorModel(BaseModel):
    identifier: str = Field(description="Stable identifier accepted by hide remove.")
    selector: str = Field(description="CSS selector hidden by the persistent style.")
    builtin: bool = Field(description="Whether this immutable selector is built in.")


class HideAddRequest(BaseModel):
    selector: str = Field(min_length=1, description="CSS selector to hide.")


class HideAddResponse(BaseModel):
    identifier: str = Field(description="Stable selector identifier.")
    selector: str = Field(description="Normalized CSS selector that was added.")
    scope: str = Field(description="Profile name, or session-only when ephemeral.")


class HideRemoveRequest(BaseModel):
    identifier_or_selector: str = Field(
        min_length=1, description="Selector identifier or exact CSS selector to remove."
    )


class HideRemoveResponse(BaseModel):
    removed: bool = Field(description="True when a user selector was removed.")
    scope: str = Field(description="Profile name, or session-only when ephemeral.")


class HideListResponse(BaseModel):
    selectors: list[HideSelectorModel] = Field(description="Active hide selectors.")
    count: int = Field(description="Number of active selectors including the builtin.")
    scope: str = Field(description="Profile name, or session-only when ephemeral.")
