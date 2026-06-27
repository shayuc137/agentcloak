"""Cross-process spawn lock tests.

Verifies that the atomic lockfile mechanism prevents multiple daemon
spawns from concurrent auto-start attempts.

GPT Pro review finding P4 (2026-06-27).
"""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from agentcloak.core.process import (
    release_spawn_lock,
    try_acquire_spawn_lock,
)


@pytest.fixture
def lock_path(tmp_path: Path) -> Path:
    return tmp_path / "spawn.lock"


class TestTryAcquireSpawnLock:
    """Atomic lockfile creation."""

    def test_acquire_creates_file(self, lock_path: Path) -> None:
        assert try_acquire_spawn_lock(lock_path) is True
        assert lock_path.exists()
        data = json.loads(lock_path.read_text())
        assert data["pid"] == os.getpid()
        assert isinstance(data["ts"], float)

    def test_second_acquire_fails(self, lock_path: Path) -> None:
        assert try_acquire_spawn_lock(lock_path) is True
        assert try_acquire_spawn_lock(lock_path) is False

    def test_release_allows_reacquire(self, lock_path: Path) -> None:
        assert try_acquire_spawn_lock(lock_path) is True
        release_spawn_lock(lock_path)
        assert not lock_path.exists()
        assert try_acquire_spawn_lock(lock_path) is True

    def test_release_nonexistent_is_safe(self, lock_path: Path) -> None:
        release_spawn_lock(lock_path)

    def test_stale_lock_cleaned_on_dead_pid(self, lock_path: Path) -> None:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps({"pid": 999999999, "ts": time.time()}))
        assert try_acquire_spawn_lock(lock_path) is False
        assert not lock_path.exists()

    def test_stale_lock_cleaned_on_old_timestamp(self, lock_path: Path) -> None:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps({"pid": os.getpid(), "ts": time.time() - 60}))
        assert try_acquire_spawn_lock(lock_path) is False
        assert not lock_path.exists()

    def test_corrupted_lock_cleaned(self, lock_path: Path) -> None:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("not json")
        assert try_acquire_spawn_lock(lock_path) is False
        assert not lock_path.exists()

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        deep_lock = tmp_path / "a" / "b" / "spawn.lock"
        assert try_acquire_spawn_lock(deep_lock) is True
        assert deep_lock.exists()
