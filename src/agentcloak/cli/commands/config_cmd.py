"""Config command — read/write configuration with sources.

The five verbs (``set``/``get``/``unset``/``list``/``add``/``remove``)
mirror ``git config`` semantics so users with that muscle memory don't
have to context-switch. The bare ``cloak config`` invocation is
backwards-compatible and falls through to ``list``.

All writes go through :mod:`agentcloak.core.config_writer` which handles
the TOML round-trip, validation, and roll-back-on-failure machinery.
"""

from __future__ import annotations

from typing import cast

import typer

from agentcloak.cli._dispatch import emit_envelope
from agentcloak.cli.output import is_json_mode, value
from agentcloak.core.config import ConfigError, dump_config, load_config
from agentcloak.core.config_writer import (
    config_add,
    config_get,
    config_list_keys,
    config_remove,
    config_set_batch,
    config_unset,
)

__all__ = ["app"]

app = typer.Typer()


def _format_value(val: object) -> str:
    """Render a config value as a stable, copy-pasteable token.

    Strings get TOML-style double quotes so an empty value (``""``) is
    visually distinct from a missing one. Booleans render lowercase to
    match the on-disk TOML form. Lists round-trip through ``repr`` which
    keeps brackets and quotes — agents can paste the line straight into
    ``config.toml`` without re-quoting.
    """
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, str):
        return f'"{val}"'
    if isinstance(val, list):
        # ``isinstance(val, list)`` narrows to ``list[Unknown]`` under strict
        # pyright; cast to a typed shape so ``repr`` sees a known element
        # type. The element type is ``object`` because ``dump_config`` returns
        # the dataclass field value as-is (str, int, bool, or list[str]).
        typed_list = cast("list[object]", val)
        return repr(typed_list)
    return str(val)


def _emit_list() -> None:
    """Shared rendering for the bare ``cloak config`` and ``cloak config list``."""
    paths, cfg = load_config()
    dumped = dump_config(cfg, paths)

    if is_json_mode():
        emit_envelope(
            {
                "ok": True,
                "seq": 0,
                "data": {
                    "config_file": str(paths.config_file),
                    "config_file_exists": paths.config_file.is_file(),
                    "fields": dumped,
                },
            }
        )
        return

    # ``git config -l --show-origin``-style listing: each line is
    # ``key = value  [source]`` with source in brackets so it's grep-able.
    # ``dump_config`` returns ``{field_name: {"value": ..., "source": ...}}``
    # — iterate items, not the dict itself, so we get the value dicts.
    lines: list[str] = [f"# {paths.config_file}"]
    # Pad the key column so sources line up vertically — ``git config --list``
    # is unaligned but the reference page promised an aligned table. Calculate
    # once over all fields so adding a long key later doesn't require a
    # template update.
    width = max((len(name) for name in dumped), default=0)
    for field_name, info in dumped.items():
        raw_value: object = info.get("value")
        source: object = info.get("source", "default")
        formatted = _format_value(raw_value)
        lines.append(f"{field_name.ljust(width)} = {formatted}  [{source}]")
    value("\n".join(lines))


def _emit_ok(message: str, extra: dict[str, object] | None = None) -> None:
    """Print a confirmation in plain or JSON mode."""
    if is_json_mode():
        data: dict[str, object] = {"message": message}
        if extra:
            data.update(extra)
        emit_envelope({"ok": True, "seq": 0, "data": data})
        return
    value(message)


def _bail(message: str) -> typer.Exit:
    from agentcloak.cli.output import error

    error(
        message,
        "run 'cloak config list' to inspect available keys",
        code="config_error",
    )
    return typer.Exit(code=1)


@app.callback(invoke_without_command=True)
def config_show(ctx: typer.Context) -> None:
    """Show merged configuration with value sources (no subcommand = list)."""
    if ctx.invoked_subcommand is not None:
        return
    _emit_list()


@app.command("list")
def config_list_cmd() -> None:
    """List all configuration fields with current value and source."""
    _emit_list()


@app.command("get")
def config_get_cmd(
    key: str = typer.Argument(..., help="Dot-notation key, e.g. 'browser.proxy'."),
) -> None:
    """Print the effective value for one configuration key."""
    paths, _ = load_config()
    try:
        result = config_get(paths, key)
    except ConfigError as exc:
        raise _bail(str(exc)) from exc
    if is_json_mode():
        emit_envelope({"ok": True, "seq": 0, "data": {"key": key, "value": result}})
        return
    value(result)


@app.command(
    "set",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def config_set_cmd(
    ctx: typer.Context,
) -> None:
    """Set one or more configuration keys.

    Usage:

      cloak config set KEY VALUE
      cloak config set KEY V1 V2 V3                # list-typed key
      cloak config set K1 V1 K2 V2                 # batch
    """
    paths, _ = load_config()
    args: list[str] = list(ctx.args)
    try:
        confirmations, restart_hint = config_set_batch(paths, args)
    except ConfigError as exc:
        raise _bail(str(exc)) from exc

    if is_json_mode():
        emit_envelope(
            {
                "ok": True,
                "seq": 0,
                "data": {
                    "updated": confirmations,
                    "restart_required": bool(restart_hint),
                },
            }
        )
        return

    message = "\n".join(confirmations)
    if restart_hint:
        message += restart_hint
    value(message)


@app.command("unset")
def config_unset_cmd(
    key: str = typer.Argument(..., help="Dot-notation key to clear."),
) -> None:
    """Remove a key from config.toml (the default takes over)."""
    paths, _ = load_config()
    try:
        message, restart_hint = config_unset(paths, key)
    except ConfigError as exc:
        raise _bail(str(exc)) from exc
    _emit_ok(message + restart_hint, {"key": key})


@app.command(
    "add",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def config_add_cmd(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="Dot-notation list key."),
) -> None:
    """Append one or more values to a list-typed key.

    Usage: cloak config add browser.extra_args --flag1 --flag2
    """
    paths, _ = load_config()
    values_list = list(ctx.args)
    try:
        message, restart_hint = config_add(paths, key, values_list)
    except ConfigError as exc:
        raise _bail(str(exc)) from exc
    _emit_ok(message + restart_hint, {"key": key})


@app.command(
    "remove",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def config_remove_cmd(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="Dot-notation list key."),
) -> None:
    """Remove the first occurrence of a value from a list-typed key.

    Usage: cloak config remove browser.extra_args --lang=ja-JP

    ``ignore_unknown_options`` keeps typer from treating user-supplied
    values that start with ``--`` (e.g. Chromium flags) as CLI options.
    """
    args: list[str] = list(ctx.args)
    if not args:
        raise _bail(f"config remove {key} requires a value to remove")
    if len(args) > 1:
        raise _bail(f"config remove {key} takes one value at a time (got {len(args)})")
    val = args[0]
    paths, _ = load_config()
    try:
        message, restart_hint = config_remove(paths, key, val)
    except ConfigError as exc:
        raise _bail(str(exc)) from exc
    _emit_ok(message + restart_hint, {"key": key, "removed": val})


@app.command("keys")
def config_keys_cmd() -> None:
    """List all valid keys ``set``/``get``/``unset`` accept."""
    keys = config_list_keys()
    if is_json_mode():
        emit_envelope({"ok": True, "seq": 0, "data": {"keys": keys}})
        return
    value("\n".join(keys))
