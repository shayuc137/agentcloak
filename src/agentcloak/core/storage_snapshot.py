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
import logging
import os
import sys
from typing import TYPE_CHECKING, Any, cast

import orjson

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "LOCALSTORAGE_DUMP_JS",
    "build_localstorage_restore_js",
    "read_storage_snapshot",
    "resolve_storage_snapshot_path",
    "write_storage_snapshot",
]

_log = logging.getLogger(__name__)

# JS snippet that returns ``JSON.stringify({o: origin, d: {...localStorage}})``
# for evaluate(). Shared between the browser base-class dump loop and the
# profile create-from-current handler so both surfaces agree on schema.
LOCALSTORAGE_DUMP_JS = (
    "JSON.stringify({o:location.origin,"
    "d:Object.fromEntries(Object.keys(localStorage)"
    ".map(k=>[k,localStorage.getItem(k)]))})"
)


def build_localstorage_restore_js(entries: dict[str, str]) -> str:
    """Build a self-contained JS snippet that writes ``entries`` into localStorage.

    ``json.dumps`` produces a valid JS object literal (no injection risk from
    the values) and the IIFE keeps our locals out of the page's global scope.
    """
    import json

    return (
        "(()=>{const d="
        + json.dumps(entries)
        + ";Object.keys(d).forEach(k=>localStorage.setItem(k,d[k]))})()"
    )


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
    (signals "this origin had no localStorage at dump time"). Written with
    ``0o600`` permissions via an atomic temp-file rename so JWTs never
    transiently live under world-readable mode.
    """
    if origin in _SKIP_ORIGINS:
        return

    existing = _read_raw(path)
    origins = existing.get("origins", {})
    origins[origin] = data

    payload = {"version": _VERSION, "origins": origins}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_secure(path, orjson.dumps(payload, option=orjson.OPT_INDENT_2))
    except OSError as exc:
        # Best-effort write, but surface the failure so a full disk / read-only
        # profile dir doesn't silently drop tokens. Callers wrap this in their
        # own suppress + logger when they can't afford to raise.
        _log.warning(
            "storage_snapshot_write_failed",
            extra={"path": str(path), "origin": origin, "error": str(exc)},
        )


def _atomic_write_secure(path: Path, payload: bytes) -> None:
    """Atomically write ``payload`` to ``path`` with 0600 permissions.

    Opens a temp file next to the target with ``O_CREAT|O_EXCL`` at mode 0600
    so the file is never world-readable, writes the payload, then atomically
    renames over the target. Windows silently ignores the mode bits (POSIX
    perms are a no-op there) but ``os.replace`` still gives us atomic swap.
    """
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if sys.platform != "win32":
        # ``0o600`` mode is only honored on POSIX; on Windows the file inherits
        # the parent directory ACLs. Users on Windows should keep the profile
        # dir under their home to constrain access.
        fd = os.open(str(tmp_path), flags, 0o600)
    else:
        fd = os.open(str(tmp_path), flags)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    os.replace(str(tmp_path), str(path))


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
