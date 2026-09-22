"""CDP commands — endpoint."""

from __future__ import annotations

import sys
from pathlib import Path  # noqa: TC003 - Typer resolves annotations at runtime.

import orjson
import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.input import invalid_input
from agentcloak.core.text_renderers import (
    render_cdp_endpoint_text,
    render_cdp_send_text,
)

__all__ = ["app"]

app = typer.Typer()


@app.command("endpoint")
def cdp_endpoint(
    page: bool = typer.Option(
        False, "--page", help="Return the current session page target endpoint."
    ),
) -> None:
    """Get the CDP WebSocket endpoint URL for jshookmcp browser_attach."""
    dispatch_text_or_json(
        DaemonClient(),
        "GET",
        "/cdp/endpoint",
        params={"page": "true"} if page else None,
        renderer=render_cdp_endpoint_text,
    )


@app.command("send")
def cdp_send(
    method: str = typer.Argument(..., help="CDP method, e.g. Runtime.evaluate."),
    params: str | None = typer.Option(
        None, "--params", help="CDP parameters as a JSON object (default: {})."
    ),
    params_file: Path | None = typer.Option(
        None,
        "--params-file",
        help="Read a UTF-8 JSON object from a file or '-' for stdin.",
    ),
    timeout: int = typer.Option(
        30000, "--timeout", min=1, help="Request timeout in milliseconds."
    ),
) -> None:
    """Send a CDP command to the current session's page."""
    if params is not None and params_file is not None:
        raise invalid_input("use only one of --params or --params-file")
    source = "--params-file" if params_file is not None else "--params"
    raw: str | bytes = params if params is not None else "{}"
    if params_file is not None:
        try:
            raw = (
                sys.stdin.buffer.read()
                if str(params_file) == "-"
                else params_file.read_bytes()
            )
        except OSError as exc:
            raise invalid_input(f"cannot read {source} '{params_file}': {exc}") from exc
    try:
        body = orjson.loads(raw)
    except orjson.JSONDecodeError as exc:
        raise invalid_input(f"{source} must be valid UTF-8 JSON") from exc
    if not isinstance(body, dict):
        raise invalid_input(f"{source} must be a JSON object")
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/cdp/send",
        json_body={"method": method, "params": body, "timeout": timeout},
        renderer=render_cdp_send_text,
    )
