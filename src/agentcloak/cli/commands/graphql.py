"""GraphQL commands (7b T1.4) — introspect a schema or send a query.

Both POST to the endpoint through the browser session (cookies + IDPI domain
check). ``introspect`` runs the standard ``__schema`` query; ``query`` sends an
arbitrary operation with optional variables.
"""

from __future__ import annotations

import orjson
import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.cli.commands._common import parse_header_list
from agentcloak.client import DaemonClient
from agentcloak.core.errors import AgentBrowserError
from agentcloak.core.text_renderers import render_graphql_text

__all__ = ["app"]

app = typer.Typer()


@app.command("introspect")
def graphql_introspect(
    url: str = typer.Argument(help="GraphQL endpoint URL."),
    header: list[str] = typer.Option(
        None, "--header", "-H", help="Request header 'Name: value' (repeatable)."
    ),
) -> None:
    """Run the standard introspection query against a GraphQL endpoint."""
    payload: dict[str, object] = {"url": url}
    headers = parse_header_list(header)
    if headers:
        payload["headers"] = headers
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/graphql/introspect",
        json_body=payload,
        renderer=render_graphql_text,
    )


@app.command("query")
def graphql_query(
    url: str = typer.Argument(help="GraphQL endpoint URL."),
    query: str = typer.Argument(help="GraphQL query or mutation document."),
    variables: str = typer.Option(
        "", "--variables", help="GraphQL variables as a JSON object string."
    ),
    header: list[str] = typer.Option(
        None, "--header", "-H", help="Request header 'Name: value' (repeatable)."
    ),
) -> None:
    """Send an arbitrary GraphQL query or mutation."""
    payload: dict[str, object] = {"url": url, "query": query}
    if variables:
        try:
            payload["variables"] = orjson.loads(variables)
        except orjson.JSONDecodeError as exc:
            raise AgentBrowserError(
                error="invalid_variables",
                hint=f"--variables is not valid JSON: {exc}",
                action="pass a JSON object, e.g. --variables '{\"id\": 1}'",
            ) from exc
    headers = parse_header_list(header)
    if headers:
        payload["headers"] = headers
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/graphql/query",
        json_body=payload,
        renderer=render_graphql_text,
    )
