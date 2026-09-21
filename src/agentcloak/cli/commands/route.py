"""Route commands (7b T1.3) — intercept network requests (abort/fulfill/continue).

Register rules that match in-flight requests by URL pattern and apply a
disposition: abort the request, fulfill it with a synthetic response, or let
it continue. Rules persist across navigations and replay onto new tabs.
"""

from __future__ import annotations

from typing import Any

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import (
    render_route_list_text,
    render_route_op_text,
    render_route_release_text,
)

__all__ = ["app"]

app = typer.Typer()


@app.command("add")
def route_add(
    pattern: str = typer.Argument(
        help=(
            "URL glob: * spans any characters; ? is a literal query separator. "
            "Without * uses substring. Include slashes explicitly: */items/?*."
        )
    ),
    action: str = typer.Option(
        "continue", "--action", help="Disposition: abort, fulfill, hold, or continue."
    ),
    hold: bool = typer.Option(
        False, "--hold", help="Pause matching requests until route release."
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
    payload: dict[str, object] = {
        "pattern": pattern,
        "action": "hold" if hold else action,
    }
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


def _render_list(data: dict[str, Any]) -> str:
    for warning in data.get("warnings", []):
        typer.echo(f"warning: {warning}", err=True)
    return render_route_list_text({**data, "warnings": []})


@app.command("list")
def route_list() -> None:
    """List active route rules."""
    dispatch_text_or_json(DaemonClient(), "GET", "/route/list", renderer=_render_list)


@app.command("release")
def route_release(
    identifier: str = typer.Argument(
        help="Held request or rule ID from route add/list."
    ),
) -> None:
    """Resume pending requests without removing their rule."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/route/release",
        json_body={"identifier": identifier},
        renderer=render_route_release_text,
    )
