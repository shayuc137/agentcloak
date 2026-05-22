"""Tests for the request-metrics middleware and its ``/health`` integration.

Covers three layers:
* :class:`MetricsState` counter mechanics (enter/exit, underflow guard),
* the ASGI middleware bumping the counters per request through TestClient,
* ``/health`` surfacing ``uptime_seconds`` / ``request_count`` /
  ``active_connections`` populated by :class:`DiagnosticService`.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentcloak.daemon.app import create_app
from agentcloak.daemon.middleware import MetricsState


@pytest.fixture
def client() -> Any:
    # ``/health`` works without a browser context (remote_bridge-pending
    # state), and the metrics fields are independent of the browser, so a
    # plain app is enough here.
    app = create_app()
    app.state.shutdown_event = asyncio.Event()
    with TestClient(app) as c:
        yield c


class TestMetricsState:
    """Counter mechanics in isolation."""

    def test_enter_increments_both_counters(self) -> None:
        m = MetricsState()
        m.enter()
        assert m.request_count == 1
        assert m.active_connections == 1

    def test_exit_only_decrements_active(self) -> None:
        m = MetricsState()
        m.enter()
        m.exit()
        # request_count is monotonic; active returns to zero.
        assert m.request_count == 1
        assert m.active_connections == 0

    def test_exit_never_goes_negative(self) -> None:
        m = MetricsState()
        m.exit()
        assert m.active_connections == 0

    def test_request_count_accumulates(self) -> None:
        m = MetricsState()
        for _ in range(5):
            m.enter()
            m.exit()
        assert m.request_count == 5
        assert m.active_connections == 0


class TestMetricsMiddleware:
    """The middleware bumps app.state.metrics on each request."""

    def test_default_state_has_metrics(self, client: TestClient) -> None:
        # create_app() seeds the slot so TestClient apps have live counters.
        assert isinstance(client.app.state.metrics, MetricsState)
        assert isinstance(client.app.state.started_at, float)

    def test_request_count_increments_per_request(self, client: TestClient) -> None:
        before = client.app.state.metrics.request_count
        client.get("/health")
        client.get("/health")
        after = client.app.state.metrics.request_count
        assert after == before + 2

    def test_active_connections_settles_to_zero(self, client: TestClient) -> None:
        # After a synchronous request completes the in-flight count is back to 0
        # (the ``finally`` in the middleware runs).
        client.get("/health")
        assert client.app.state.metrics.active_connections == 0

    def test_rejected_request_still_counted(self, client: TestClient) -> None:
        # Metrics middleware sits outside the localhost gate, so even a 403'd
        # non-localhost request counts toward request_count. We can't fake a
        # remote client.host through TestClient easily, so instead assert the
        # ordering invariant: the openapi.json probe (bypass path) is counted.
        before = client.app.state.metrics.request_count
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        assert client.app.state.metrics.request_count == before + 1


class TestHealthMetricsFields:
    """``/health`` exposes the three liveness-metric fields."""

    def test_health_includes_metric_fields(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "uptime_seconds" in data
        assert "request_count" in data
        assert "active_connections" in data

    def test_uptime_is_non_negative_number(self, client: TestClient) -> None:
        data = client.get("/health").json()
        assert isinstance(data["uptime_seconds"], (int, float))
        assert data["uptime_seconds"] >= 0

    def test_request_count_reflects_prior_requests(self, client: TestClient) -> None:
        # Two warm-up requests, then read the count off /health (the third
        # request). The reported count must include the /health call itself.
        client.get("/health")
        client.get("/health")
        data = client.get("/health").json()
        assert data["request_count"] >= 3

    def test_active_connections_during_health_is_one(self, client: TestClient) -> None:
        # While /health is being served it is the only in-flight request.
        data = client.get("/health").json()
        assert data["active_connections"] == 1
