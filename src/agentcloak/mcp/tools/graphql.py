"""GraphQL tool (7b T1.4) — introspect a schema or send a query."""

# pyright: reportUnusedFunction=false
# Tools register via @mcp.tool decorator side-effect.

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from mcp.types import ToolAnnotations

from agentcloak.core.text_renderers import render_graphql_text
from agentcloak.mcp._format import format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient

__all__ = ["register"]


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def agentcloak_graphql(
        action: Literal["introspect", "query"] = "introspect",
        url: str = "",
        query: str = "",
        variables: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        """Introspect a GraphQL schema or send an arbitrary query.

        Requests go through the browser session, so they inherit the current
        page's cookies and pass the security domain check. Introspection
        enumerates the schema's types and fields; query sends any operation.

        Actions:
          introspect — run the standard __schema introspection query
          query      — send 'query' with optional 'variables'

        Args:
            action: 'introspect' or 'query'
            url: GraphQL endpoint URL
            query: GraphQL document (for query)
            variables: GraphQL variables object (for query)
            headers: Extra request headers (e.g. an auth token)

        Returns:
            'status=<code> <json>' — the parsed data/errors, or the raw body
            when the endpoint returned non-JSON.
        """
        if action == "query":
            return await format_call(
                client.graphql_query(
                    url=url, query=query, variables=variables, headers=headers
                ),
                render_graphql_text,
            )
        return await format_call(
            client.graphql_introspect(url=url, headers=headers), render_graphql_text
        )
