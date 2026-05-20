"""GraphQL routes (7b T1.4) — introspection + query over the browser fetch.

The routes are a thin service over ``ctx.fetch``; we mock fetch to return a
canned GraphQL body and assert (a) the right operation is POSTed and (b) the
response is parsed into data/errors/raw correctly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import orjson
import pytest

from agentcloak.daemon.models import (
    GraphQLIntrospectRequest,
    GraphQLQueryRequest,
)
from agentcloak.daemon.routes.graphql import (
    _graphql_post,
    handle_graphql_introspect,
    handle_graphql_query,
)


def _ctx_returning(body: Any, *, status: int = 200) -> AsyncMock:
    """A mock ctx whose ``fetch`` returns the given body (dict → JSON-encoded)."""
    raw = body if isinstance(body, str) else orjson.dumps(body).decode()
    ctx = AsyncMock()
    ctx.fetch = AsyncMock(return_value={"status": status, "body": raw})
    ctx.seq = 7
    return ctx


class TestGraphQLPost:
    @pytest.mark.asyncio
    async def test_parses_data_field(self) -> None:
        ctx = _ctx_returning({"data": {"me": {"id": 1}}})

        result = await _graphql_post(ctx, "https://x/graphql", {"query": "{me}"}, {})

        assert result["status"] == 200
        assert result["data"] == {"me": {"id": 1}}
        assert result["errors"] is None
        assert result["raw"] == ""

    @pytest.mark.asyncio
    async def test_parses_errors_field(self) -> None:
        ctx = _ctx_returning({"errors": [{"message": "nope"}]})

        result = await _graphql_post(ctx, "https://x/graphql", {"query": "{x}"}, {})

        assert result["errors"] == [{"message": "nope"}]
        assert result["data"] is None

    @pytest.mark.asyncio
    async def test_non_json_body_falls_back_to_raw(self) -> None:
        ctx = _ctx_returning("<html>500</html>", status=500)

        result = await _graphql_post(ctx, "https://x/graphql", {"query": "{x}"}, {})

        assert result["status"] == 500
        assert result["raw"] == "<html>500</html>"
        assert result["data"] is None

    @pytest.mark.asyncio
    async def test_posts_json_with_content_type(self) -> None:
        ctx = _ctx_returning({"data": {}})

        await _graphql_post(
            ctx, "https://x/graphql", {"query": "{x}"}, {"X-Auth": "tok"}
        )

        ctx.fetch.assert_awaited_once()
        kwargs = ctx.fetch.await_args.kwargs
        assert kwargs["method"] == "POST"
        assert kwargs["headers"]["Content-Type"] == "application/json"
        assert kwargs["headers"]["X-Auth"] == "tok"
        assert orjson.loads(kwargs["body"]) == {"query": "{x}"}


class TestIntrospectRoute:
    @pytest.mark.asyncio
    async def test_sends_introspection_query(self) -> None:
        ctx = _ctx_returning({"data": {"__schema": {"types": []}}})
        body = GraphQLIntrospectRequest(url="https://x/graphql")

        envelope = await handle_graphql_introspect(body, ctx)

        # Wrapped in the standard ok-envelope.
        assert envelope["ok"] is True
        assert envelope["data"]["data"] == {"__schema": {"types": []}}
        sent = orjson.loads(ctx.fetch.await_args.kwargs["body"])
        assert "__schema" in sent["query"]


class TestQueryRoute:
    @pytest.mark.asyncio
    async def test_query_with_variables(self) -> None:
        ctx = _ctx_returning({"data": {"user": {"name": "fufu"}}})
        body = GraphQLQueryRequest(
            url="https://x/graphql",
            query="query($id:ID!){user(id:$id){name}}",
            variables={"id": "42"},
        )

        envelope = await handle_graphql_query(body, ctx)

        assert envelope["data"]["data"] == {"user": {"name": "fufu"}}
        sent = orjson.loads(ctx.fetch.await_args.kwargs["body"])
        assert sent["variables"] == {"id": "42"}

    @pytest.mark.asyncio
    async def test_query_without_variables_omits_key(self) -> None:
        ctx = _ctx_returning({"data": {}})
        body = GraphQLQueryRequest(url="https://x/graphql", query="{ping}")

        await handle_graphql_query(body, ctx)

        sent = orjson.loads(ctx.fetch.await_args.kwargs["body"])
        assert "variables" not in sent
