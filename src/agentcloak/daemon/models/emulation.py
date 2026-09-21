"""Page environment, viewport and raw CDP request models."""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, Field, model_validator

from agentcloak.core.emulation import PageEmulation

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


class EmulationRequest(PageEmulation):
    reset: bool = False

    @model_validator(mode="after")
    def validate_reset(self) -> Self:
        if self.reset and any(
            value is not None
            for key, value in self.model_dump().items()
            if key != "reset"
        ):
            raise ValueError("reset cannot be combined with emulation settings")
        return self


class ViewportRequest(BaseModel):
    width: int = Field(ge=1, le=16384)
    height: int = Field(ge=1, le=16384)
    dpr: float | None = Field(None, gt=0, allow_inf_nan=False)


class ViewportResponse(BaseModel):
    width: int
    height: int
    dpr: float


class CdpSendRequest(BaseModel):
    method: str = Field(pattern=r"^[A-Za-z]+\.[A-Za-z0-9]+$")
    params: dict[str, Any] = Field(default_factory=dict)
    timeout: int = Field(30000, gt=0, description="Request timeout in milliseconds.")


class CdpSendResponse(BaseModel):
    result: Any
