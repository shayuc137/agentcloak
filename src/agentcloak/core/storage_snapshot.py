"""localStorage snapshot persistence — per-origin key/value dump and restore.

Mirrors the cookie_snapshot module: resolve path, incremental write, tolerant
read.  The on-disk format is versioned so future additions (sessionStorage,
IndexedDB metadata) can be bolted on without breaking existing snapshots.

File format::

    {
      "version": 1,
      "origins": {
        "https://example.com": {"token": "jwt_abc", "user": "shayu"},
        "https://app.local:5173": {"session_id": "xyz"}
      }
    }
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, cast

import orjson

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "read_storage_snapshot",
    "resolve_storage_snapshot_path",
    "write_storage_snapshot",
]

_SNAPSHOT_NAME = "localStorage-snapshot.json"
_VERSION = 1

# Origins that should never be persisted — they have no meaningful storage.
_SKIP_ORIGINS = frozenset({"null", "", "about:blank"})


def resolve_storage_snapshot_path(profile_dir: Path) -> Path:
    """Return the snapshot file path inside a profile directory."""
    return profile_dir / _SNAPSHOT_NAME


def write_storage_snapshot(
    path: Path,
    origin: str,
    data: dict[str, str],
) -> None:
    """Merge *data* for *origin* into the snapshot file and persist.

    Incremental: reads the existing file (if any), replaces the entry for
    *origin*, and writes back.  An empty *data* dict still records the origin
    (signals "this origin had no localStorage at dump time").
    """
    if origin in _SKIP_ORIGINS:
        return

    existing = _read_raw(path)
    origins = existing.get("origins", {})
    origins[origin] = data

    payload = {"version": _VERSION, "origins": origins}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2))
        path.chmod(0o600)
    except OSError:
        pass


def read_storage_snapshot(path: Path) -> dict[str, dict[str, str]]:
    """Return the per-origin localStorage data, or empty dict if missing."""
    raw = _read_raw(path)
    origins: Any = raw.get("origins")
    if not isinstance(origins, dict):
        return {}
    return cast("dict[str, dict[str, str]]", origins)


def _read_raw(path: Path) -> dict[str, Any]:
    """Best-effort JSON read; returns ``{}`` on any failure."""
    with contextlib.suppress(OSError, orjson.JSONDecodeError):
        payload = orjson.loads(path.read_bytes())
        if isinstance(payload, dict):
            return cast("dict[str, Any]", payload)
    return {}
