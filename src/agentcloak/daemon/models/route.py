"""Pydantic models for network route-interception routes (7b T1.3).

A route rule matches in-flight requests by URL pattern (plus optional
resource-type / method filters) and applies one of three dispositions:
``abort`` (fail it), ``fulfill`` (synthetic response), or ``continue`` (let it
through). ``/route/add`` registers a rule, ``/route/remove`` drops one (or all),
``/route/list`` reports the active set.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = [
    "RouteAddRequest",
    "RouteListResponse",
    "RouteOpResponse",
    "RouteReleaseRequest",
    "RouteReleaseResponse",
    "RouteRemoveRequest",
]


class RouteAddRequest(BaseModel):
    """Register a network interception rule."""

    pattern: str = Field(
        description="URL glob ('*' = any run of chars; no '*' = substring match)."
    )
    action: str = Field(
        "continue",
        description="Disposition: 'abort', 'fulfill', 'hold', or 'continue'.",
    )
    resource_type: str = Field(
        "", description="Only match this resource type (document, xhr, image, ...)."
    )
    method: str = Field("", description="Only match this HTTP method (GET, POST, ...).")
    status: int = Field(0, description="Response status for 'fulfill' (default 200).")
    content_type: str = Field(
        "", description="Content-Type header for a 'fulfill' response."
    )
    body: str = Field("", description="Response body for a 'fulfill' response.")


class RouteRemoveRequest(BaseModel):
    pattern: str = Field(
        "", description="Pattern to remove; blank removes ALL active rules."
    )


class RouteOpResponse(BaseModel):
    identifier: str | None = Field(
        None, description="Rule ID; release resumes its pending requests."
    )
    pattern: str | None = Field(None, description="Pattern acted on (None = all).")
    removed: int = Field(0, description="Number of rules removed (remove only).")
    count: int = Field(description="Active rule count after the operation.")


class RouteListResponse(BaseModel):
    rules: list[dict[str, object]] = Field(description="Active route rules.")
    count: int = Field(description="Number of active route rules.")
    pending: list[dict[str, str]] = Field(
        default_factory=list[dict[str, str]],
        description="Held requests with identifier, rule_id, and URL.",
    )
    warnings: list[str] = Field(
        default_factory=list, description="Rules with no observed matches."
    )


class RouteReleaseRequest(BaseModel):
    identifier: str = Field(
        min_length=1, description="Pending request ID or rule ID from route add/list."
    )


class RouteReleaseResponse(BaseModel):
    identifier: str = Field(description="Released request or rule ID.")
    released: int = Field(description="Number of held requests resumed.")
