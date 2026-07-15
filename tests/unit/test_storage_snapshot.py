"""localStorage snapshot persistence — write, read, merge, edge cases."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import orjson

from agentcloak.core.storage_snapshot import (
    read_storage_snapshot,
    resolve_storage_snapshot_path,
    write_storage_snapshot,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_resolve_path_inside_profile(tmp_path: Path) -> None:
    assert resolve_storage_snapshot_path(tmp_path / "myprofile") == (
        tmp_path / "myprofile" / "localStorage-snapshot.json"
    )


def test_write_creates_file_with_correct_format(tmp_path: Path) -> None:
    path = tmp_path / "localStorage-snapshot.json"

    write_storage_snapshot(path, "https://example.com", {"token": "jwt123"})

    payload = orjson.loads(path.read_bytes())
    assert payload == {
        "version": 1,
        "origins": {"https://example.com": {"token": "jwt123"}},
    }
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o777 == 0o600


def test_write_merges_with_existing(tmp_path: Path) -> None:
    path = tmp_path / "localStorage-snapshot.json"

    write_storage_snapshot(path, "https://a.com", {"k1": "v1"})
    write_storage_snapshot(path, "https://b.com", {"k2": "v2"})

    data = read_storage_snapshot(path)
    assert data == {
        "https://a.com": {"k1": "v1"},
        "https://b.com": {"k2": "v2"},
    }


def test_write_replaces_origin_entirely(tmp_path: Path) -> None:
    path = tmp_path / "localStorage-snapshot.json"

    write_storage_snapshot(path, "https://a.com", {"old": "data"})
    write_storage_snapshot(path, "https://a.com", {"new": "data"})

    data = read_storage_snapshot(path)
    assert data == {"https://a.com": {"new": "data"}}


def test_write_skips_null_origin(tmp_path: Path) -> None:
    path = tmp_path / "localStorage-snapshot.json"

    write_storage_snapshot(path, "null", {"k": "v"})

    assert not path.exists()


def test_write_skips_blank_origin(tmp_path: Path) -> None:
    path = tmp_path / "localStorage-snapshot.json"

    write_storage_snapshot(path, "about:blank", {"k": "v"})

    assert not path.exists()


def test_write_skips_empty_origin(tmp_path: Path) -> None:
    path = tmp_path / "localStorage-snapshot.json"

    write_storage_snapshot(path, "", {"k": "v"})

    assert not path.exists()


def test_read_missing_file_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "nope.json"

    assert read_storage_snapshot(path) == {}


def test_read_corrupt_file_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "localStorage-snapshot.json"
    path.write_text("not json!!!")

    assert read_storage_snapshot(path) == {}


def test_read_wrong_structure_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "localStorage-snapshot.json"
    path.write_bytes(orjson.dumps(["not", "a", "dict"]))

    assert read_storage_snapshot(path) == {}


def test_read_missing_origins_key_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "localStorage-snapshot.json"
    path.write_bytes(orjson.dumps({"version": 1}))

    assert read_storage_snapshot(path) == {}


def test_write_empty_data_still_records_origin(tmp_path: Path) -> None:
    path = tmp_path / "localStorage-snapshot.json"

    write_storage_snapshot(path, "https://empty.com", {})

    data = read_storage_snapshot(path)
    assert data == {"https://empty.com": {}}
