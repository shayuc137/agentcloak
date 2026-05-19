"""Pydantic models for tab-management routes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

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
    url: str | None = None


class TabCloseRequest(BaseModel):
    tab_id: int


class TabSwitchRequest(BaseModel):
    tab_id: int


class TabOpResponse(BaseModel):
    """Generic tab-operation response (new/close/switch).

    The backend returns a small payload like ``{tab_id, url, title}`` that
    differs slightly per operation, so we keep this open-ended rather than
    fanning out one model per route — agents read whichever fields they
    care about and ignore the rest.
    """

    model_config = ConfigDict(extra="allow")
