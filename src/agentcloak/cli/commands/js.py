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
    code: str = typer.Argument(help="JavaScript code to evaluate."),
    world: str = typer.Option(
        "main", help="Execution context: 'main' (page globals) or 'isolated'."
    ),
) -> None:
    """Evaluate JavaScript in the page context."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/evaluate",
        json_body={"js": code, "world": world},
        renderer=render_evaluate_text,
    )
