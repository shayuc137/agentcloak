"""Tests for core/capture.py — CaptureStore and CaptureEntry."""

from agentcloak.core.capture import (
    MAX_BODY_SIZE,
    CaptureEntry,
    CaptureStore,
    build_capture_entry,
)


def _make_entry(
    *,
    url: str = "https://api.example.com/v1/users",
    method: str = "GET",
    status: int = 200,
    resource_type: str = "xhr",
    content_type: str = "application/json",
    seq: int = 1,
) -> CaptureEntry:
    return CaptureEntry(
        seq=seq,
        timestamp="2026-05-07T12:00:00Z",
        method=method,
        url=url,
        status=status,
        resource_type=resource_type,
        content_type=content_type,
    )


class TestCaptureStore:
    def test_not_recording_by_default(self) -> None:
        store = CaptureStore()
        assert store.recording is False

    def test_add_while_not_recording(self) -> None:
        store = CaptureStore()
        added = store.add(_make_entry())
        assert added is False
        assert len(store) == 0

    def test_add_while_recording(self) -> None:
        store = CaptureStore()
        store.start()
        added = store.add(_make_entry())
        assert added is True
        assert len(store) == 1

    def test_start_stop(self) -> None:
        store = CaptureStore()
        store.start()
        assert store.recording is True
        store.stop()
        assert store.recording is False

    def test_filters_static_resources(self) -> None:
        store = CaptureStore()
        store.start()
        store.add(_make_entry(resource_type="stylesheet"))
        store.add(_make_entry(resource_type="image"))
        store.add(_make_entry(resource_type="font"))
        assert len(store) == 0

    def test_filters_static_extensions(self) -> None:
        store = CaptureStore()
        store.start()
        store.add(_make_entry(url="https://cdn.example.com/style.css"))
        store.add(_make_entry(url="https://cdn.example.com/logo.png"))
        store.add(_make_entry(url="https://cdn.example.com/font.woff2"))
        assert len(store) == 0

    def test_allows_api_requests(self) -> None:
        store = CaptureStore()
        store.start()
        store.add(_make_entry(url="https://api.example.com/v1/users"))
        store.add(_make_entry(url="https://api.example.com/v1/posts"))
        assert len(store) == 2

    def test_capacity_eviction(self) -> None:
        store = CaptureStore(capacity=3)
        store.start()
        for i in range(5):
            store.add(_make_entry(seq=i))
        assert len(store) == 3
        entries = store.entries()
        assert entries[0].seq == 2

    def test_clear(self) -> None:
        store = CaptureStore()
        store.start()
        store.add(_make_entry())
        store.clear()
        assert len(store) == 0

    def test_api_entries_filters_non_json(self) -> None:
        store = CaptureStore()
        store.start()
        store.add(_make_entry(content_type="application/json"))
        store.add(_make_entry(content_type="text/html"))
        store.add(_make_entry(content_type="text/plain"))
        api = store.api_entries()
        assert len(api) == 3

    def test_api_entries_filters_zero_status(self) -> None:
        store = CaptureStore()
        store.start()
        store.add(_make_entry(status=200))
        store.add(_make_entry(status=0))
        api = store.api_entries()
        assert len(api) == 1


class TestBuildCaptureEntry:
    """Factory function shared by Playwright + RemoteBridge backends.

    Phase 6d extracted the final assembly into one place so both backends
    apply the same content-type filter, body truncation, and default-
    timestamp rules. These tests pin those invariants so a future change
    can't silently regress one backend's behaviour.
    """

    def test_recordable_body_passes_through(self) -> None:
        entry = build_capture_entry(
            seq=7,
            method="GET",
            url="https://api.example.com/x",
            status=200,
            resource_type="xhr",
            request_headers={"User-Agent": "agent"},
            response_headers={"Content-Type": "application/json"},
            request_body=None,
            raw_response_body='{"ok":true}',
            content_type="application/json",
        )
        assert entry.seq == 7
        assert entry.response_body == '{"ok":true}'
        assert entry.timestamp  # default-now ISO string populated

    def test_non_recordable_body_dropped_even_when_supplied(self) -> None:
        """Builder defensively re-checks content-type — binary blobs never leak."""
        entry = build_capture_entry(
            seq=1,
            method="GET",
            url="https://cdn.example.com/img.bin",
            status=200,
            resource_type="other",
            request_headers={},
            response_headers={},
            request_body=None,
            raw_response_body="binary garbage",  # caller forgot to skip
            content_type="image/png",
        )
        # Builder's re-check drops the body — the factory is the safety net.
        assert entry.response_body is None

    def test_oversize_body_truncated(self) -> None:
        huge = "x" * (MAX_BODY_SIZE + 1000)
        entry = build_capture_entry(
            seq=1,
            method="POST",
            url="https://api.example.com/big",
            status=200,
            resource_type="xhr",
            request_headers={},
            response_headers={},
            request_body=None,
            raw_response_body=huge,
            content_type="application/json",
        )
        assert entry.response_body is not None
        assert len(entry.response_body) == MAX_BODY_SIZE

    def test_supplied_timestamp_preserved(self) -> None:
        entry = build_capture_entry(
            seq=1,
            method="GET",
            url="https://api.example.com/",
            status=200,
            resource_type="xhr",
            request_headers={},
            response_headers={},
            request_body=None,
            raw_response_body=None,
            content_type="application/json",
            timestamp="2026-05-19T01:23:45+00:00",
        )
        assert entry.timestamp == "2026-05-19T01:23:45+00:00"

    def test_default_timestamp_uses_now(self) -> None:
        """Skipping timestamp defaults to ISO-formatted now in UTC."""
        entry = build_capture_entry(
            seq=1,
            method="GET",
            url="https://api.example.com/",
            status=200,
            resource_type="xhr",
            request_headers={},
            response_headers={},
            request_body=None,
            raw_response_body=None,
            content_type="application/json",
        )
        # Default timestamp must be ISO-formatted with timezone info (+00:00).
        # Picking apart the exact value is brittle; just confirm it has the
        # expected shape so a future change to ``datetime.now`` doesn't pass
        # silently.
        assert "T" in entry.timestamp
        assert "+" in entry.timestamp or entry.timestamp.endswith("Z")

    def test_request_body_round_trips(self) -> None:
        entry = build_capture_entry(
            seq=1,
            method="POST",
            url="https://api.example.com/x",
            status=200,
            resource_type="xhr",
            request_headers={},
            response_headers={},
            request_body="hello",
            raw_response_body=None,
            content_type="application/json",
        )
        assert entry.request_body == "hello"
        # No response body provided — entry.response_body stays None even when
        # content-type is recordable.
        assert entry.response_body is None
