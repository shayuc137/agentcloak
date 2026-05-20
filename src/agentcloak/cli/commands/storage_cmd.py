"""Storage commands — localStorage / sessionStorage CRUD (7a R4)."""

from __future__ import annotations

import typer

from agentcloak.cli._dispatch import dispatch_text_or_json
from agentcloak.client import DaemonClient
from agentcloak.core.text_renderers import render_storage_text

__all__ = ["app"]

app = typer.Typer()

_TYPE_HELP = "Storage area: 'local' (persistent) or 'session' (per-tab)."


@app.command("get")
def storage_get(
    key: str | None = typer.Argument(
        None, help="Key to read. Omit to dump all entries."
    ),
    storage_type: str = typer.Option("local", "--type", "-t", help=_TYPE_HELP),
) -> None:
    """Read a storage value (or dump the whole area)."""
    body: dict[str, object] = {"type": storage_type}
    if key is not None:
        body["key"] = key
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/storage/get",
        json_body=body,
        renderer=render_storage_text,
    )


@app.command("set")
def storage_set(
    key: str = typer.Argument(..., help="Key to write."),
    value: str = typer.Argument(..., help="Value to store."),
    storage_type: str = typer.Option("local", "--type", "-t", help=_TYPE_HELP),
) -> None:
    """Write a key/value into a storage area."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/storage/set",
        json_body={"type": storage_type, "key": key, "value": value},
        renderer=render_storage_text,
    )


@app.command("delete")
def storage_delete(
    key: str = typer.Argument(..., help="Key to remove."),
    storage_type: str = typer.Option("local", "--type", "-t", help=_TYPE_HELP),
) -> None:
    """Remove a single key from a storage area."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/storage/delete",
        json_body={"type": storage_type, "key": key},
        renderer=render_storage_text,
    )


@app.command("clear")
def storage_clear(
    storage_type: str = typer.Option("local", "--type", "-t", help=_TYPE_HELP),
) -> None:
    """Empty an entire storage area."""
    dispatch_text_or_json(
        DaemonClient(),
        "POST",
        "/storage/clear",
        json_body={"type": storage_type},
        renderer=render_storage_text,
    )
