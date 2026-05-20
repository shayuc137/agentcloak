"""Route commands (7b T1.3) — intercept network requests (abort/fulfill/continue).

Register rules that match in-flight requests by URL pattern and apply a
disposition: abort the request, fulfill it with a synthetic response, or let
it continue. Rules persist across navigations and replay onto new tabs.
"""

from __future__ import annotations

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import (
    render_route_list_text,
    render_route_op_text,
)

__all__ = ["app"]

app = typer.Typer()


@app.command("add")
def route_add(
    pattern: str = typer.Argument(
        help="URL glob ('*' = any chars; no '*' = substring match)."
    ),
    action: str = typer.Option(
        "continue", "--action", help="Disposition: abort, fulfill, or continue."
    ),
    resource_type: str = typer.Option(
        "", "--resource-type", help="Only match this resource type (xhr, image, ...)."
    ),
    method: str = typer.Option(
        "", "--method", help="Only match this HTTP method (GET, POST, ...)."
    ),
    status: int = typer.Option(
        0, "--status", help="Response status for a 'fulfill' rule (default 200)."
    ),
    content_type: str = typer.Option(
        "", "--content-type", help="Content-Type for a 'fulfill' response."
    ),
    body: str = typer.Option(
        "", "--body", help="Response body for a 'fulfill' response."
    ),
) -> None:
    """Add a network route rule."""
    payload: dict[str, object] = {"pattern": pattern, "action": action}
    if resource_type:
        payload["resource_type"] = resource_type
    if method:
        payload["method"] = method
    if status:
        payload["status"] = status
    if content_type:
        payload["content_type"] = content_type
    if body:
        payload["body"] = body
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/route/add",
        json_body=payload,
        renderer=render_route_op_text,
    )


@app.command("remove")
def route_remove(
    pattern: str = typer.Argument(
        "", help="Pattern to remove; omit to remove ALL rules."
    ),
) -> None:
    """Remove a route rule by pattern, or all rules when no pattern is given."""
    body: dict[str, object] = {}
    if pattern:
        body["pattern"] = pattern
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/route/remove",
        json_body=body,
        renderer=render_route_op_text,
    )


@app.command("list")
def route_list() -> None:
    """List active route rules."""
    dispatch_text_or_json(
        DaemonClient(), "GET", "/route/list", renderer=render_route_list_text
    )
