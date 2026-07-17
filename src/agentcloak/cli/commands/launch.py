"""Launch command — hot-switch the daemon's active browser tier.

Before v0.3.0 ``agentcloak launch`` was a synonym for "restart the daemon
with these flags". As of the dynamic-tier-switch work it just POSTs to
``/launch``: the daemon stays up, only the active context swaps.
"""

from __future__ import annotations

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import render_launch_text

__all__ = ["app", "launch"]

app = typer.Typer()


@app.callback(invoke_without_command=True)
def launch(
    tier: str = typer.Option(
        "auto",
        "--tier",
        "-t",
        help=("Browser tier: auto (default: cloak), cloak, playwright, remote_bridge."),
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        "-p",
        help=(
            "Named browser profile for persistent cookies/state. Only "
            "applies to local tiers."
        ),
    ),
    no_profile: bool = typer.Option(
        False,
        "--no-profile",
        help="Explicitly switch to no profile, discarding the current one.",
    ),
) -> None:
    """Hot-switch the daemon's active browser tier (no restart)."""
    body: dict[str, object] = {"tier": tier}
    if profile is not None:
        body["profile"] = profile
    if no_profile:
        body["no_profile"] = True
    dispatch_text_or_json(
        DaemonClient(), "POST", "/launch", json_body=body, renderer=render_launch_text
    )
