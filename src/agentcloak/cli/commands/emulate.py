"""Session-scoped page environment overrides."""

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.emulation import ColorScheme, Pointer
from agentcloak.core.text_renderers import render_emulation_text

app = typer.Typer()


@app.callback(invoke_without_command=True)
def emulate(
    ctx: typer.Context,
    color_scheme: ColorScheme | None = typer.Option(None, "--color-scheme"),
    reduced_motion: bool | None = typer.Option(
        None, "--reduced-motion/--no-reduced-motion"
    ),
    pointer: Pointer | None = typer.Option(
        None, "--pointer", help="Requires a headed local browser."
    ),
) -> None:
    """Set session overrides, or show them when no options are supplied."""
    body = {
        key: value
        for key, value in {
            "color_scheme": color_scheme,
            "reduced_motion": reduced_motion,
            "pointer": pointer,
        }.items()
        if value is not None
    }
    if ctx.invoked_subcommand:
        if body:
            raise typer.BadParameter("reset cannot be combined with emulation settings")
        return
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/emulation",
        json_body=body,
        renderer=render_emulation_text,
    )


@app.command("reset")
def reset() -> None:
    """Clear media and pointer overrides; retain viewport and HTTP headers."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/emulation",
        json_body={"reset": True},
        renderer=render_emulation_text,
    )
