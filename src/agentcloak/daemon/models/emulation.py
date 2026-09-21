"""Pydantic models for emulation routes (7b T1.2).

Currently a single capability: extra HTTP headers injected on every request
(custom Authorization / tokens for API debugging). ``POST
/emulation/headers`` sets the active set; passing an empty map clears it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

__all__ = ["HeadersRequest", "HeadersResponse"]


class HeadersRequest(BaseModel):
    """Set the extra HTTP headers applied to every request."""

    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Header name → value map. Empty clears all overrides.",
    )


class HeadersResponse(BaseModel):
    headers: dict[str, str] = Field(description="The now-active extra headers.")
    count: int = Field(description="Number of active extra headers.")


class ViewportRequest(BaseModel):
    width: int = Field(ge=1, le=16384)
    height: int = Field(ge=1, le=16384)


class ViewportResponse(BaseModel):
    width: int
    height: int


class CdpSendRequest(BaseModel):
    method: str = Field(pattern=r"^[A-Za-z]+\.[A-Za-z0-9]+$")
    params: dict[str, Any] = Field(default_factory=dict)
    timeout: int = Field(30000, gt=0, description="Request timeout in milliseconds.")


class CdpSendResponse(BaseModel):
    result: Any
