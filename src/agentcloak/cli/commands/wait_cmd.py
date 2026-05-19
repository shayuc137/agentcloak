"""Wait command — conditional waiting on selector / URL / load / JS / ms."""

from __future__ import annotations

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.cli.output import error
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import render_wait_text

__all__ = ["app"]

app = typer.Typer(invoke_without_command=True)


@app.callback(invoke_without_command=True)
def do_wait(
    selector: str | None = typer.Option(
        None, "--selector", "-s", help="CSS selector to wait for."
    ),
    url: str | None = typer.Option(
        None, "--url", help="URL pattern to wait for (glob)."
    ),
    load: str | None = typer.Option(
        None,
        "--load",
        help="Load state: load, domcontentloaded, networkidle.",
    ),
    js: str | None = typer.Option(
        None, "--js", help="JS expression that must return truthy."
    ),
    ms: int | None = typer.Option(None, "--ms", help="Milliseconds to sleep."),
    timeout: int | None = typer.Option(
        None,
        "--timeout",
        help="Timeout in milliseconds (default: config.action_timeout).",
    ),
    state: str = typer.Option(
        "visible",
        "--state",
        help="Element state: visible/hidden/attached/detached.",
    ),
) -> None:
    """Wait for a condition before continuing."""
    # Determine condition from flags. ``error()`` raises SystemExit so the
    # assignment is always reachable when we keep going.
    condition: str
    val: str
    if selector is not None:
        condition, val = "selector", selector
    elif url is not None:
        condition, val = "url", url
    elif load is not None:
        condition, val = "load", load
    elif js is not None:
        condition, val = "js", js
    elif ms is not None:
        condition, val = "ms", str(ms)
    else:
        error("no wait condition", "provide --selector, --url, --load, --js, or --ms")
        return
    body: dict[str, object] = {
        "condition": condition,
        "value": val,
        "state": state,
    }
    if timeout is not None:
        body["timeout"] = timeout
    dispatch_text_or_json(
        DaemonClient(), "POST", "/wait", json_body=body, renderer=render_wait_text
    )
