"""Pydantic models for GraphQL routes (7b T1.4).

GraphQL endpoints are a frequent reverse-engineering target. ``/graphql/introspect``
fires the standard ``__schema`` introspection query; ``/graphql/query`` sends an
arbitrary operation. Both reuse the browser ``fetch`` path so the request
inherits the session's cookies and goes through the IDPI domain check.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "GraphQLIntrospectRequest",
    "GraphQLQueryRequest",
    "GraphQLResponse",
]


class GraphQLIntrospectRequest(BaseModel):
    """Run the standard introspection query against a GraphQL endpoint."""

    url: str = Field(description="GraphQL endpoint URL.")
    headers: dict[str, str] = Field(
        default_factory=dict, description="Extra request headers (e.g. auth token)."
    )


class GraphQLQueryRequest(BaseModel):
    """Send an arbitrary GraphQL operation."""

    url: str = Field(description="GraphQL endpoint URL.")
    query: str = Field(description="GraphQL query or mutation document.")
    variables: dict[str, Any] = Field(
        default_factory=dict, description="GraphQL variables object."
    )
    headers: dict[str, str] = Field(
        default_factory=dict, description="Extra request headers (e.g. auth token)."
    )


class GraphQLResponse(BaseModel):
    status: int = Field(description="HTTP status of the GraphQL POST.")
    data: Any = Field(None, description="Parsed 'data' field from the response.")
    errors: Any = Field(None, description="Parsed 'errors' field, if any.")
    raw: str = Field("", description="Raw response body (when JSON parsing failed).")
