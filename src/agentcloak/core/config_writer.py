"""Read/write user-facing config knobs in ``config.toml``.

The CLI's ``cloak config set/get/unset/add/remove`` subcommands all funnel
through this module. The actual on-disk write reuses the existing
``_read_toml`` / ``_serialise_toml`` helpers in ``core.config`` so the
file shape stays consistent with what ``write_example_config`` produces.

We never touch env vars or defaults — the writer only mutates
``~/.agentcloak/config.toml``. The precedence chain ``env > toml > default``
stays the same; users see "(env override active)" hints in ``config list``
if their setting is being shadowed.
"""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING, Any, cast

from agentcloak.core.config import (
    FIELD_SCHEMA,
    AgentcloakConfig,
    ConfigError,
    read_toml,
    serialise_toml,
)

if TYPE_CHECKING:
    from agentcloak.core.config import Paths

__all__ = [
    "config_add",
    "config_get",
    "config_list_keys",
    "config_remove",
    "config_set_batch",
    "config_unset",
    "parse_batch_args",
]

# Sections that require a daemon restart to take effect. Telling the user
# this up-front saves them from setting a value and silently watching it
# be ignored until they remember to restart.
_RESTART_SECTIONS: frozenset[str] = frozenset({"daemon", "browser"})


def config_list_keys() -> list[str]:
    """Return the sorted list of dot-notation keys that ``set`` accepts."""
    return sorted(FIELD_SCHEMA.keys())


def _resolve_key(key: str) -> tuple[str, str, type]:
    """Look up ``key`` in :data:`FIELD_SCHEMA`, raising on unknowns."""
    if key not in FIELD_SCHEMA:
        # Trim the suggestion list to a handful of close-ish matches so the
        # error stays scannable. ``startswith`` covers the most common
        # mistake (forgetting the section prefix).
        suggestions = [k for k in FIELD_SCHEMA if k.endswith("." + key.split(".")[-1])]
        if not suggestions:
            suggestions = [k for k in FIELD_SCHEMA if k.startswith(key)]
        hint = f"; did you mean: {', '.join(suggestions[:5])}?" if suggestions else ""
        raise ConfigError(
            f"Unknown config key: {key!r}. "
            f"Use 'cloak config list' to see all keys{hint}"
        )
    return FIELD_SCHEMA[key]


def _parse_scalar(value: str, field_type: type) -> Any:
    """Coerce a CLI string to the dataclass field's Python type."""
    if field_type is bool:
        low = value.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
        raise ConfigError(f"Expected a bool (true/false), got {value!r}")
    if field_type is int:
        try:
            return int(value)
        except ValueError as exc:
            raise ConfigError(f"Expected an integer, got {value!r}") from exc
    if field_type is float:
        try:
            return float(value)
        except ValueError as exc:
            raise ConfigError(f"Expected a float, got {value!r}") from exc
    # str (and any other type we didn't special-case) — pass through verbatim.
    return value


def _safe_write_and_validate(paths: Paths, sections: dict[str, dict[str, Any]]) -> None:
    """Write ``sections`` to ``config.toml`` with a roll-back on validation failure.

    ``AgentcloakConfig._validate`` runs at the tail of ``load_config``. We
    write the new file, immediately reload to validate, and restore the
    previous bytes (or delete the file if it didn't exist) when the
    reload raises :class:`ConfigError`. The user sees the original error
    and ``config.toml`` is left in a known-good state.
    """
    from agentcloak.core.config import load_config

    backup: bytes | None = None
    existed = paths.config_file.is_file()
    if existed:
        backup = paths.config_file.read_bytes()

    _write_full(paths, sections)
    try:
        # Same reason as ``config_get``: respect the caller's ``paths`` so
        # validation hits the file we actually wrote.
        load_config(root=paths.root)
    except ConfigError:
        if backup is not None:
            paths.config_file.write_bytes(backup)
        elif paths.config_file.is_file():
            with contextlib.suppress(OSError):
                paths.config_file.unlink()
        raise


def _write_full(paths: Paths, sections: dict[str, dict[str, Any]]) -> None:
    """Persist ``sections`` to ``config.toml``, tightening perms best-effort."""
    paths.ensure_dirs()
    paths.config_file.write_text(serialise_toml(sections), encoding="utf-8")
    # 0o600 keeps the bridge token (also stored here) out of other users'
    # view. Best-effort on Windows where chmod is a no-op.
    with contextlib.suppress(OSError):
        os.chmod(str(paths.config_file), 0o600)


def _load_all_sections(paths: Paths) -> dict[str, dict[str, Any]]:
    """Read every ``[section]`` table from ``config.toml``."""
    raw: dict[str, Any] = read_toml(paths.config_file)
    out: dict[str, dict[str, Any]] = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            out[k] = dict(cast("dict[str, Any]", v))
    return out


def _restart_hint(touched_sections: set[str]) -> str:
    """Return ``" (restart daemon to apply)"`` when applicable, else ``""``."""
    if touched_sections & _RESTART_SECTIONS:
        return " (restart daemon to apply)"
    return ""


def _format_value(value: Any) -> str:
    """Render a value for confirmation output (e.g. ``"socks5://host:1080"``)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        items = cast("list[Any]", value)
        return "[" + ", ".join(_format_value(v) for v in items) + "]"
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def config_get(paths: Paths, key: str) -> str:
    """Return the in-effect value for ``key`` formatted for display.

    Reads the merged config (env > toml > default) so the user sees the
    same value the daemon will actually use. Source attribution is omitted
    here — use ``cloak config list`` when you need the source column.
    """
    from agentcloak.core.config import load_config

    section, field, _ = _resolve_key(key)
    _, cfg = load_config(root=paths.root)
    sub_cfg = getattr(cfg, section)
    value = getattr(sub_cfg, field)
    return _format_value(value)


def parse_batch_args(args: list[str]) -> list[tuple[str, list[str]]]:
    """Split ``cloak config set`` positional args into ``(key, values)`` pairs.

    Algorithm walks left-to-right. The next token after a scalar key is the
    value. List-typed keys greedily consume tokens until the next token
    matches another :data:`FIELD_SCHEMA` key (or input ends). The 'next
    token is a known key' check is what makes ``set browser.extra_args
    --flag1 --flag2 browser.headless false`` parse correctly.
    """
    if not args:
        raise ConfigError(
            "config set requires at least one KEY VALUE pair "
            "(e.g. 'cloak config set browser.headless false')"
        )

    pairs: list[tuple[str, list[str]]] = []
    i = 0
    n = len(args)
    while i < n:
        key = args[i]
        _, _, field_type = _resolve_key(key)
        i += 1
        if field_type is list:
            values: list[str] = []
            while i < n and args[i] not in FIELD_SCHEMA:
                values.append(args[i])
                i += 1
            if not values:
                raise ConfigError(f"{key} is a list — provide at least one value")
        else:
            if i >= n:
                raise ConfigError(f"{key} requires a value")
            values = [args[i]]
            i += 1
        pairs.append((key, values))
    return pairs


def config_set_batch(paths: Paths, args: list[str]) -> tuple[list[str], str]:
    """Apply a batch of ``key value [key value ...]`` assignments.

    Returns ``(confirmation_lines, restart_hint)``. The hint is empty when
    no touched section requires a daemon restart.
    """
    pairs = parse_batch_args(args)

    sections = _load_all_sections(paths)
    confirmations: list[str] = []
    touched_sections: set[str] = set()

    for key, values in pairs:
        section, field_name, field_type = _resolve_key(key)
        if field_type is list:
            parsed: Any = list(values)
        else:
            parsed = _parse_scalar(values[0], field_type)
        sections.setdefault(section, {})[field_name] = parsed
        touched_sections.add(section)
        confirmations.append(f"{key} = {_format_value(parsed)}")

    # ``_safe_write_and_validate`` writes then immediately reloads — if the
    # new combination is invalid (e.g. a port out of range), it rolls the
    # file back to the previous bytes so the daemon never sees junk.
    _safe_write_and_validate(paths, sections)
    return confirmations, _restart_hint(touched_sections)


def config_unset(paths: Paths, key: str) -> tuple[str, str]:
    """Remove ``key`` from ``config.toml``. Returns ``(message, restart_hint)``."""
    section, field_name, _ = _resolve_key(key)
    sections = _load_all_sections(paths)
    section_table = sections.get(section, {})

    if field_name not in section_table:
        return f"{key} is already at its default", ""

    del section_table[field_name]
    # Drop empty sections so the file doesn't accumulate ``[bridge]`` with
    # no entries after an unset.
    if not section_table and section in sections:
        del sections[section]

    _safe_write_and_validate(paths, sections)
    return (
        f"{key} unset (will use default on next daemon start)",
        _restart_hint({section}),
    )


def config_add(paths: Paths, key: str, values: list[str]) -> tuple[str, str]:
    """Append ``values`` to a list-typed ``key``."""
    section, field_name, field_type = _resolve_key(key)
    if field_type is not list:
        raise ConfigError(
            f"{key} is not a list — use 'cloak config set {key} VALUE' instead"
        )
    if not values:
        raise ConfigError(f"{key} add requires at least one value")

    sections = _load_all_sections(paths)
    current_raw: Any = sections.setdefault(section, {}).get(field_name, [])
    if not isinstance(current_raw, list):
        # Defensive: if the on-disk file had the wrong shape (e.g. a string),
        # don't silently coerce — make the user fix it explicitly.
        raise ConfigError(
            f"{key} exists in config.toml but is not a list "
            f"(got {type(current_raw).__name__}). Run 'cloak config unset {key}' first."
        )
    current: list[Any] = list(cast("list[Any]", current_raw))
    current.extend(values)
    sections[section][field_name] = current

    _safe_write_and_validate(paths, sections)
    return f"{key} = {_format_value(current)}", _restart_hint({section})


def config_remove(paths: Paths, key: str, value: str) -> tuple[str, str]:
    """Remove the first occurrence of ``value`` from a list-typed ``key``."""
    section, field_name, field_type = _resolve_key(key)
    if field_type is not list:
        raise ConfigError(
            f"{key} is not a list — use 'cloak config unset {key}' to clear it"
        )

    sections = _load_all_sections(paths)
    section_table = sections.get(section, {})
    current_raw: Any = section_table.get(field_name)
    if current_raw is None:
        raise ConfigError(f"{key} is empty — nothing to remove")
    if not isinstance(current_raw, list):
        raise ConfigError(
            f"{key} exists in config.toml but is not a list "
            f"(got {type(current_raw).__name__})"
        )
    current: list[Any] = list(cast("list[Any]", current_raw))
    if value not in current:
        raise ConfigError(
            f"{value!r} is not present in {key} (current: {_format_value(current)})"
        )
    current.remove(value)

    if current:
        section_table[field_name] = current
    else:
        # Drop the key entirely so subsequent ``add`` starts from an empty
        # list rather than ``[]`` lingering on disk. Net effect for the
        # user is the same as unset.
        del section_table[field_name]
        if not section_table and section in sections:
            del sections[section]

    _safe_write_and_validate(paths, sections)
    return f"{key} = {_format_value(current)}", _restart_hint({section})


# Re-export so callers don't need to import from two modules.
__all__ += ["AgentcloakConfig", "ConfigError"]
