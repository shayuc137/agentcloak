"""CDP commands — endpoint."""

from __future__ import annotations

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
def cdp_endpoint() -> None:
    """Get the CDP WebSocket endpoint URL for jshookmcp browser_attach."""
    dispatch_text_or_json(
        DaemonClient(), "GET", "/cdp/endpoint", renderer=render_cdp_endpoint_text
    )


@app.command("send")
def cdp_send(
    method: str = typer.Argument(..., help="CDP method, e.g. Runtime.evaluate."),
    params: str = typer.Option(
        "{}", "--params", help="CDP parameters as a JSON object."
    ),
    timeout: int = typer.Option(
        30000, "--timeout", min=1, help="Request timeout in milliseconds."
    ),
) -> None:
    """Send a CDP command to the current session's page."""
    try:
        body = orjson.loads(params)
    except orjson.JSONDecodeError as exc:
        raise invalid_input("--params must be valid JSON") from exc
    if not isinstance(body, dict):
        raise invalid_input("--params must be a JSON object")
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/cdp/send",
        json_body={"method": method, "params": body, "timeout": timeout},
        renderer=render_cdp_send_text,
    )
