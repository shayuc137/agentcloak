"""Cookie snapshot persistence and Playwright import normalization."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import orjson

from agentcloak.core.errors import AgentBrowserError

if TYPE_CHECKING:
    from pathlib import Path

    from agentcloak.core.config import Paths

__all__ = [
    "normalize_cookies_for_playwright",
    "read_cookie_snapshot",
    "resolve_cookie_snapshot_path",
    "write_cookie_snapshot",
]

_SNAPSHOT_NAME = "cookies-snapshot.json"

_SAME_SITE_VALUES = {
    "strict": "Strict",
    "lax": "Lax",
    "none": "None",
    "no_restriction": "None",
    "unspecified": "None",
}


def normalize_cookies_for_playwright(
    cookies: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Normalize browser cookie variants at the Playwright import boundary."""
    normalized: list[dict[str, Any]] = []
    skipped = 0

    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            skipped += 1
            continue

        item: dict[str, Any] = {"name": name, "value": value}
        for field in ("domain", "path", "url"):
            field_value = cookie.get(field)
            if isinstance(field_value, str):
                item[field] = field_value

        if cookie.get("session") is True:
            item["expires"] = -1
        else:
            expires = cookie.get("expires")
            if isinstance(expires, (int, float)) and not isinstance(expires, bool):
                item["expires"] = expires
            else:
                expiration_date = cookie.get("expirationDate")
                if isinstance(expiration_date, (int, float)) and not isinstance(
                    expiration_date, bool
                ):
                    item["expires"] = float(expiration_date)
                else:
                    item["expires"] = -1

        for field in ("httpOnly", "secure"):
            field_value = cookie.get(field)
            if isinstance(field_value, bool):
                item[field] = field_value

        same_site = cookie.get("sameSite")
        if isinstance(same_site, str):
            mapped_same_site = _SAME_SITE_VALUES.get(same_site.casefold())
            if mapped_same_site is not None:
                item["sameSite"] = mapped_same_site

        normalized.append(item)

    return normalized, skipped


def resolve_cookie_snapshot_path(paths: Paths, active_profile: str | None) -> Path:
    """Resolve the snapshot beside an active profile or under the data root."""
    if active_profile:
        return paths.profiles_dir / active_profile / _SNAPSHOT_NAME
    return paths.root / _SNAPSHOT_NAME


def write_cookie_snapshot(path: Path, data: dict[str, Any]) -> None:
    """Persist the export payload without changing its round-trip shape."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2))
        path.chmod(0o600)
    except OSError as exc:
        raise AgentBrowserError(
            error="cookies_snapshot_write_failed",
            hint=f"Could not write cookie snapshot '{path}': {exc}",
            action="check the snapshot directory permissions and retry cookies export",
        ) from exc


def read_cookie_snapshot(path: Path) -> list[dict[str, Any]]:
    """Read cookies from an export payload or a bare cookie array."""
    if not path.is_file():
        raise AgentBrowserError(
            error="cookies_snapshot_not_found",
            hint=f"Cookie snapshot '{path}' does not exist",
            action="run cookies export first, then retry cookies restore",
        )
    try:
        payload = cast("object", orjson.loads(path.read_bytes()))
    except (OSError, orjson.JSONDecodeError) as exc:
        raise AgentBrowserError(
            error="cookies_snapshot_invalid",
            hint=f"Cookie snapshot '{path}' could not be read as JSON: {exc}",
            action="run cookies export again to replace the snapshot",
        ) from exc

    cookies: object
    if isinstance(payload, dict):
        cookies = cast("dict[object, object]", payload).get("cookies")
    else:
        cookies = payload
    if not isinstance(cookies, list):
        raise AgentBrowserError(
            error="cookies_snapshot_invalid",
            hint=f"Cookie snapshot '{path}' does not contain a cookie array",
            action="run cookies export again to replace the snapshot",
        )
    cookie_items = cast("list[object]", cookies)
    if not all(isinstance(cookie, dict) for cookie in cookie_items):
        raise AgentBrowserError(
            error="cookies_snapshot_invalid",
            hint=f"Cookie snapshot '{path}' does not contain a cookie array",
            action="run cookies export again to replace the snapshot",
        )
    return cast("list[dict[str, Any]]", cookie_items)
