"""Cookie snapshot path, persistence, and restore helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import orjson
import pytest

from agentcloak.core.config import Paths
from agentcloak.core.cookie_snapshot import (
    normalize_cookies_for_playwright,
    read_cookie_snapshot,
    resolve_cookie_snapshot_path,
    write_cookie_snapshot,
)
from agentcloak.core.errors import AgentBrowserError

if TYPE_CHECKING:
    from pathlib import Path


def test_snapshot_path_uses_active_profile(tmp_path: Path) -> None:
    paths = Paths(root=tmp_path)

    assert resolve_cookie_snapshot_path(paths, "dos") == (
        tmp_path / "profiles" / "dos" / "cookies-snapshot.json"
    )


def test_snapshot_path_falls_back_to_data_root(tmp_path: Path) -> None:
    paths = Paths(root=tmp_path)

    assert resolve_cookie_snapshot_path(paths, None) == (
        tmp_path / "cookies-snapshot.json"
    )


def test_snapshot_round_trip_preserves_export_payload(tmp_path: Path) -> None:
    path = tmp_path / "profile" / "cookies-snapshot.json"
    data = {"cookies": [{"name": "sid", "value": "abc"}], "count": 1}

    write_cookie_snapshot(path, data)

    assert orjson.loads(path.read_bytes()) == data
    assert read_cookie_snapshot(path) == data["cookies"]
    import sys

    if sys.platform != "win32":
        assert path.stat().st_mode & 0o777 == 0o600


def test_missing_snapshot_has_actionable_three_field_error(tmp_path: Path) -> None:
    path = tmp_path / "cookies-snapshot.json"

    with pytest.raises(AgentBrowserError) as caught:
        read_cookie_snapshot(path)

    assert caught.value.to_dict() == {
        "ok": False,
        "error": "cookies_snapshot_not_found",
        "hint": f"Cookie snapshot '{path}' does not exist",
        "action": "run cookies export first, then retry cookies restore",
    }


def test_normalize_chrome_cookie_for_playwright() -> None:
    cookies = [
        {
            "domain": "h5api.m.taobao.com",
            "expirationDate": 1795752619.62036,
            "hostOnly": True,
            "httpOnly": False,
            "name": "arms_uid",
            "path": "/",
            "sameSite": "unspecified",
            "secure": False,
            "session": False,
            "storeId": "0",
            "value": "abc",
        }
    ]

    normalized, skipped = normalize_cookies_for_playwright(cookies)

    assert normalized == [
        {
            "name": "arms_uid",
            "value": "abc",
            "domain": "h5api.m.taobao.com",
            "path": "/",
            "expires": 1795752619.62036,
            "httpOnly": False,
            "secure": False,
            "sameSite": "None",
        }
    ]
    assert skipped == 0


def test_normalize_cdp_cookie_drops_unsupported_fields() -> None:
    normalized, skipped = normalize_cookies_for_playwright(
        [
            {
                "name": "sid",
                "value": "token",
                "domain": ".example.com",
                "path": "/app",
                "expires": 1795752619,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
                "priority": "High",
                "size": 8,
            }
        ]
    )

    assert normalized == [
        {
            "name": "sid",
            "value": "token",
            "domain": ".example.com",
            "path": "/app",
            "expires": 1795752619,
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
        }
    ]
    assert skipped == 0


def test_normalize_playwright_url_cookie_preserves_supported_shape() -> None:
    cookie = {
        "name": "sid",
        "value": "abc",
        "url": "https://example.com/app",
        "expires": -1,
        "httpOnly": True,
        "secure": True,
        "sameSite": "Strict",
    }

    normalized, skipped = normalize_cookies_for_playwright([cookie])

    assert normalized == [cookie]
    assert skipped == 0


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("strict", "Strict"),
        ("STRICT", "Strict"),
        ("lax", "Lax"),
        ("LAX", "Lax"),
        ("none", "None"),
        ("no_restriction", "None"),
        ("unspecified", "None"),
    ],
)
def test_normalize_same_site_variants(source: str, expected: str) -> None:
    normalized, _ = normalize_cookies_for_playwright(
        [{"name": "sid", "value": "abc", "sameSite": source}]
    )

    assert normalized[0]["sameSite"] == expected


def test_normalize_omits_missing_or_unknown_same_site() -> None:
    normalized, _ = normalize_cookies_for_playwright(
        [
            {"name": "missing", "value": "a"},
            {"name": "unknown", "value": "b", "sameSite": "invalid"},
        ]
    )

    assert all("sameSite" not in cookie for cookie in normalized)


def test_normalize_session_and_missing_expiration_as_session_cookies() -> None:
    normalized, _ = normalize_cookies_for_playwright(
        [
            {
                "name": "session",
                "value": "a",
                "session": True,
                "expirationDate": 1795752619,
            },
            {"name": "missing", "value": "b"},
        ]
    )

    assert [cookie["expires"] for cookie in normalized] == [-1, -1]


def test_normalize_skips_entries_missing_name_or_value() -> None:
    normalized, skipped = normalize_cookies_for_playwright(
        [
            {"value": "missing-name"},
            {"name": "missing-value"},
            {"name": "valid", "value": ""},
        ]
    )

    assert normalized == [{"name": "valid", "value": "", "expires": -1}]
    assert skipped == 2
