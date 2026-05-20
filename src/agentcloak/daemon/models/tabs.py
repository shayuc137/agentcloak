"""Pydantic models for tab-management routes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "TabCloseRequest",
    "TabListResponse",
    "TabNewRequest",
    "TabOpResponse",
    "TabSwitchRequest",
]


class TabListResponse(BaseModel):
    tabs: list[dict[str, Any]]
    count: int


class TabNewRequest(BaseModel):
    url: str | None = Field(
        None, description="URL to open in the new tab; blank opens about:blank."
    )


class TabCloseRequest(BaseModel):
    tab_id: int = Field(description="Tab id (from tab list) to close.")


class TabSwitchRequest(BaseModel):
    tab_id: int = Field(description="Tab id (from tab list) to make active.")


class TabOpResponse(BaseModel):
    """Generic tab-operation response (new/close/switch).

    The backend returns a small payload like ``{tab_id, url, title}`` that
    differs slightly per operation, so we keep this open-ended rather than
    fanning out one model per route — agents read whichever fields they
    care about and ignore the rest.
    """

    model_config = ConfigDict(extra="allow")
