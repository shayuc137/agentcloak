"""Script commands (7b T1.1) — inject/remove/list init scripts.

Init scripts run before any page script on every navigation — the standard
hook point for reverse engineering. Inject raw JS or a named hook preset
(fetch/xhr/json_parse/crypto/timing).
"""

from __future__ import annotations

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import (
    render_script_add_text,
    render_script_list_text,
    render_script_remove_text,
)

__all__ = ["app"]

app = typer.Typer()


@app.command("add")
def script_add(
    js: str = typer.Argument(
        "", help="Raw JavaScript to run before page scripts (omit if using --preset)."
    ),
    preset: str = typer.Option(
        "",
        "--preset",
        help="Built-in hook preset: fetch, xhr, json_parse, crypto, timing.",
    ),
) -> None:
    """Inject an init script; prints the identifier for later removal."""
    body: dict[str, object] = {}
    if preset:
        body["preset"] = preset
    if js:
        body["js"] = js
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/script/add",
        json_body=body,
        renderer=render_script_add_text,
    )


@app.command("remove")
def script_remove(
    identifier: str = typer.Argument(help="Identifier returned by 'script add'."),
) -> None:
    """Remove a previously-injected init script by identifier."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/script/remove",
        json_body={"identifier": identifier},
        renderer=render_script_remove_text,
    )


@app.command("list")
def script_list() -> None:
    """List active init scripts (identifier + source preview)."""
    dispatch_text_or_json(
        DaemonClient(), "GET", "/script/list", renderer=render_script_list_text
    )
