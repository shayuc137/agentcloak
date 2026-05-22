"""JavaScript execution command."""

from __future__ import annotations

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import render_evaluate_text

__all__ = ["app"]

app = typer.Typer()


@app.command("evaluate")
def js_evaluate(
    code: str = typer.Argument(
        "", help="JavaScript code to evaluate (omit when using --preset)."
    ),
    world: str = typer.Option(
        "main", help="Execution context: 'main' (page globals) or 'isolated'."
    ),
    preset: str = typer.Option(
        "",
        "--preset",
        help=(
            "Run a reverse-engineering preset instead of JS (forced to main "
            "world): vue_inspect, react_inspect, jwt_decode, cookie_parse, "
            "storage_dump."
        ),
    ),
) -> None:
    """Evaluate JavaScript in the page context."""
    body: dict[str, object] = {"world": world}
    if preset:
        body["preset"] = preset
    else:
        body["js"] = code
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/evaluate",
        json_body=body,
        renderer=render_evaluate_text,
    )
