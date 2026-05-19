"""Network capture store — full request/response recording."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "CaptureEntry",
    "CaptureStore",
    "build_capture_entry",
    "is_recordable_content",
    "truncate_body",
]

MAX_BODY_SIZE = 100_000

_SKIP_RESOURCE_TYPES = frozenset(
    {"stylesheet", "image", "font", "media", "manifest", "other"}
)

_SKIP_EXTENSIONS = frozenset(
    {
        ".js",
        ".css",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".ico",
        ".mp4",
        ".webp",
    }
)

_RECORDABLE_CONTENT_TYPES = frozenset(
    {
        "application/json",
        "text/html",
        "text/plain",
        "text/xml",
        "application/xml",
        "application/x-www-form-urlencoded",
    }
)


@dataclass(frozen=True)
class CaptureEntry:
    """A captured network request/response pair."""

    seq: int
    timestamp: str
    method: str
    url: str
    status: int
    resource_type: str
    request_headers: dict[str, str] = field(default_factory=dict[str, str])
    response_headers: dict[str, str] = field(default_factory=dict[str, str])
    request_body: str | None = None
    response_body: str | None = None
    content_type: str = ""
    duration_ms: float = 0.0


def _should_skip(url: str, resource_type: str) -> bool:
    if resource_type in _SKIP_RESOURCE_TYPES:
        return True
    path = url.split("?", 1)[0].split("#", 1)[0]
    dot = path.rfind(".")
    if dot != -1:
        ext = path[dot:].lower()
        if ext in _SKIP_EXTENSIONS:
            return True
    return False


def truncate_body(body: str | None) -> str | None:
    """Truncate body to MAX_BODY_SIZE."""
    if body is None:
        return None
    if len(body) > MAX_BODY_SIZE:
        return body[:MAX_BODY_SIZE]
    return body


def is_recordable_content(content_type: str) -> bool:
    ct = content_type.split(";", 1)[0].strip().lower()
    return ct in _RECORDABLE_CONTENT_TYPES


def build_capture_entry(
    *,
    seq: int,
    method: str,
    url: str,
    status: int,
    resource_type: str,
    request_headers: dict[str, str],
    response_headers: dict[str, str],
    request_body: str | None,
    raw_response_body: str | None,
    content_type: str,
    timestamp: str | None = None,
    duration_ms: float = 0.0,
) -> CaptureEntry:
    """Construct a :class:`CaptureEntry` with shared post-processing.

    The Playwright and RemoteBridge backends collect raw network events
    in completely different shapes (Page response handlers vs. CDP event
    stitching), but the final assembly is identical: drop the response
    body if its content-type isn't on the recordable allow-list,
    truncate to ``MAX_BODY_SIZE``, and default the timestamp to UTC now.
    Centralising the assembly here keeps the two backends honest.

    ``raw_response_body`` is the decoded UTF-8 string the backend already
    fetched (or ``None`` if the body was never retrieved). The caller is
    expected to gate fetching on :func:`is_recordable_content` for
    performance — this factory still re-checks defensively so a slipped
    binary blob never lands in the store.
    """
    if timestamp is None:
        timestamp = datetime.now(UTC).isoformat()
    body: str | None = None
    if raw_response_body is not None and is_recordable_content(content_type):
        body = truncate_body(raw_response_body)
    return CaptureEntry(
        seq=seq,
        timestamp=timestamp,
        method=method,
        url=url,
        status=status,
        resource_type=resource_type,
        request_headers=request_headers,
        response_headers=response_headers,
        request_body=request_body,
        response_body=body,
        content_type=content_type,
        duration_ms=duration_ms,
    )


class CaptureStore:
    """Stores captured network entries with auto-filtering."""

    def __init__(self, *, capacity: int = 5000) -> None:
        self._capacity = capacity
        self._entries: deque[CaptureEntry] = deque()
        self._recording = False

    @property
    def recording(self) -> bool:
        return self._recording

    def start(self) -> None:
        self._recording = True

    def stop(self) -> None:
        self._recording = False

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)

    def add(self, entry: CaptureEntry) -> bool:
        """Add an entry if recording and not filtered. Returns True if added."""
        if not self._recording:
            return False
        if _should_skip(entry.url, entry.resource_type):
            return False
        if len(self._entries) >= self._capacity:
            self._entries.popleft()
        self._entries.append(entry)
        return True

    def entries(self) -> list[CaptureEntry]:
        return list(self._entries)

    def entries_by_domain(self, domain: str) -> list[CaptureEntry]:
        result: list[CaptureEntry] = []
        for e in self._entries:
            parts = e.url.split("/")
            if len(parts) > 2 and domain in parts[2]:
                result.append(e)
        return result

    def api_entries(self) -> list[CaptureEntry]:
        """Return only JSON API entries (filtered for pattern analysis)."""
        return [
            e
            for e in self._entries
            if is_recordable_content(e.content_type) and e.status > 0
        ]

    def find_latest(self, url: str, method: str = "GET") -> CaptureEntry | None:
        """Return the most recent entry matching url and method (case-insensitive)."""
        method_upper = method.upper()
        for entry in reversed(self._entries):
            if entry.method.upper() == method_upper and entry.url == url:
                return entry
        return None

    def to_dict_list(self) -> list[dict[str, Any]]:
        return [
            {
                "seq": e.seq,
                "timestamp": e.timestamp,
                "method": e.method,
                "url": e.url,
                "status": e.status,
                "resource_type": e.resource_type,
                "content_type": e.content_type,
                "duration_ms": e.duration_ms,
                "request_headers": e.request_headers,
                "response_headers": e.response_headers,
                "request_body": e.request_body,
                "response_body": e.response_body,
            }
            for e in self._entries
        ]
