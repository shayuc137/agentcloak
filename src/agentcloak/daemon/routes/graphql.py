"""GraphQL routes (7b T1.4) — introspection + arbitrary query.

Both routes POST to the GraphQL endpoint through the browser ``fetch`` path, so
the request carries the session's cookies and passes the IDPI domain check
(``SecureBrowserContext.fetch``). No browser manager is needed — this is a thin
service over the existing fetch atom.
"""

from __future__ import annotations

import contextlib
from typing import Any, cast

import orjson
from fastapi import APIRouter

from agentcloak.daemon.dependencies import BrowserCtxDep  # noqa: TC001
from agentcloak.daemon.models import (
    GraphQLIntrospectRequest,
    GraphQLQueryRequest,
    GraphQLResponse,
    OkEnvelope,
)
from agentcloak.daemon.routes._helpers import _ok

__all__ = ["router"]

router = APIRouter()

# Standard GraphQL introspection query — enough to enumerate types, fields,
# args, and the root operation names. Trimmed of the deep ``ofType`` recursion
# beyond a few levels (which is rarely needed and bloats the payload).
_INTROSPECTION_QUERY = """
query IntrospectionQuery {
  __schema {
    queryType { name }
    mutationType { name }
    subscriptionType { name }
    types {
      kind
      name
      description
      fields(includeDeprecated: true) {
        name
        args { name type { kind name ofType { kind name } } }
        type { kind name ofType { kind name ofType { kind name } } }
      }
    }
  }
}
"""


async def _graphql_post(
    ctx: Any, url: str, payload: dict[str, Any], headers: dict[str, str]
) -> dict[str, Any]:
    """POST a GraphQL ``payload`` via the browser fetch and parse the result."""
    req_headers = {"Content-Type": "application/json", **headers}
    result = await ctx.fetch(
        url,
        method="POST",
        body=orjson.dumps(payload).decode(),
        headers=req_headers,
    )
    status = int(result.get("status", 0) or 0)
    raw = str(result.get("body", "") or "")
    parsed: Any = None
    with contextlib.suppress(orjson.JSONDecodeError, ValueError):
        parsed = orjson.loads(raw) if raw else None

    if isinstance(parsed, dict):
        body: dict[str, Any] = cast("dict[str, Any]", parsed)
        return {
            "status": status,
            "data": body.get("data"),
            "errors": body.get("errors"),
            "raw": "" if "data" in body or "errors" in body else raw,
        }
    # Non-JSON or non-object response — hand back the raw body so the agent can
    # see what the endpoint actually returned (HTML error page, etc.).
    return {"status": status, "data": None, "errors": None, "raw": raw}


@router.post("/graphql/introspect", response_model=OkEnvelope[GraphQLResponse])
async def handle_graphql_introspect(
    body: GraphQLIntrospectRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    data = await _graphql_post(
        ctx, body.url, {"query": _INTROSPECTION_QUERY}, body.headers
    )
    return _ok(data, seq=ctx.seq)


@router.post("/graphql/query", response_model=OkEnvelope[GraphQLResponse])
async def handle_graphql_query(
    body: GraphQLQueryRequest, ctx: BrowserCtxDep
) -> dict[str, Any]:
    payload: dict[str, Any] = {"query": body.query}
    if body.variables:
        payload["variables"] = body.variables
    data = await _graphql_post(ctx, body.url, payload, body.headers)
    return _ok(data, seq=ctx.seq)
