"""JavaScript execution command."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 - Typer needs the runtime type.

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.cli.output import error
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
    file: Path | None = typer.Option(
        None,
        "--file",
        help="Read JavaScript from a UTF-8 file (for multiline probes).",
    ),
) -> None:
    """Evaluate JavaScript in the page context."""
    source_count = int(bool(code)) + int(bool(preset)) + int(file is not None)
    if source_count == 0:
        error(
            "no JavaScript source provided",
            "pass inline CODE, --file PATH, or --preset NAME",
        )
    if source_count > 1:
        error(
            "multiple JavaScript sources provided",
            "use exactly one of inline CODE, --file, or --preset",
        )

    body: dict[str, object] = {"world": world}
    if preset:
        body["preset"] = preset
    else:
        if file is not None:
            try:
                code = file.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                error(
                    f"cannot read JavaScript file '{file}': {exc}",
                    "check the path and ensure the file is UTF-8",
                )
            if not code:
                error(
                    f"JavaScript file '{file}' is empty",
                    "add a script to the file or pass inline CODE",
                )
        body["js"] = code
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/evaluate",
        json_body=body,
        renderer=render_evaluate_text,
    )
