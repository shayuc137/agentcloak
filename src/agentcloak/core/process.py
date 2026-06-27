"""Cross-platform process utilities."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def pid_alive(pid: int) -> bool:
    """Check whether *pid* is alive, cross-platform."""
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


_SPAWN_LOCK_STALE_S = 30.0


def try_acquire_spawn_lock(lock_path: Path) -> bool:
    """Atomically create *lock_path* with the current PID.

    Returns True if the lock was acquired. If the file already exists,
    checks for staleness (owner PID dead or older than 30 s) and cleans
    it before returning False.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        _maybe_clean_stale_lock(lock_path)
        return False
    try:
        payload = json.dumps({"pid": os.getpid(), "ts": time.time()})
        os.write(fd, payload.encode())
    finally:
        os.close(fd)
    return True


def release_spawn_lock(lock_path: Path) -> None:
    """Remove *lock_path* if it exists."""
    with contextlib.suppress(OSError):
        lock_path.unlink(missing_ok=True)


def _maybe_clean_stale_lock(lock_path: Path) -> None:
    """Remove *lock_path* if the owner PID is dead or the lock is too old."""
    try:
        content = lock_path.read_text()
        data = json.loads(content)
        owner_pid = int(data["pid"])
        ts = float(data["ts"])
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        with contextlib.suppress(OSError):
            lock_path.unlink(missing_ok=True)
        return

    if not pid_alive(owner_pid) or (time.time() - ts) > _SPAWN_LOCK_STALE_S:
        with contextlib.suppress(OSError):
            lock_path.unlink(missing_ok=True)
