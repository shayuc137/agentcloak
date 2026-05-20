"""CLI command tests for the 7b T1 reverse-engineering batch.

Same approach as ``test_7a_cli.py``: the daemon HTTP layer is mocked at
``DaemonClient._send_sync`` and commands are driven through ``CliRunner``,
asserting on the rendered text output (and the outgoing request shape where
it carries behaviour, e.g. the route action or a script preset).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from agentcloak.cli import output as cli_output
from agentcloak.cli.app import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_cli_mode() -> Any:
    cli_output.set_json_mode(enabled=False)
    cli_output.set_pretty(enabled=False)
    yield
    cli_output.set_json_mode(enabled=False)
    cli_output.set_pretty(enabled=False)


def _envelope(data: dict[str, Any], *, seq: int = 1) -> dict[str, Any]:
    return {"ok": True, "seq": seq, "data": data}


# ---------------------------------------------------------------------------
# script
# ---------------------------------------------------------------------------


class TestScriptCli:
    def test_add_prints_identifier(self) -> None:
        payload = _envelope({"identifier": "id-9", "preset": None})
        with patch("agentcloak.client.DaemonClient._send_sync", return_value=payload):
            result = runner.invoke(app, ["script", "add", "console.log(1)"])
        assert result.exit_code == 0, result.stdout
        assert "id-9" in result.stdout

    def test_add_preset_sends_preset(self) -> None:
        payload = _envelope({"identifier": "id-p", "preset": "fetch"})
        with patch(
            "agentcloak.client.DaemonClient._send_sync", return_value=payload
        ) as m:
            result = runner.invoke(app, ["script", "add", "--preset", "fetch"])
        assert result.exit_code == 0, result.stdout
        body = m.call_args.kwargs["json_body"]
        assert body["preset"] == "fetch"
        assert "id-p" in result.stdout

    def test_remove(self) -> None:
        payload = _envelope({"removed": True})
        with patch("agentcloak.client.DaemonClient._send_sync", return_value=payload):
            result = runner.invoke(app, ["script", "remove", "id-9"])
        assert result.exit_code == 0, result.stdout
        assert "removed" in result.stdout

    def test_list(self) -> None:
        payload = _envelope(
            {
                "scripts": [{"identifier": "id-1", "source": "console.log(1)"}],
                "count": 1,
            }
        )
        with patch("agentcloak.client.DaemonClient._send_sync", return_value=payload):
            result = runner.invoke(app, ["script", "list"])
        assert result.exit_code == 0, result.stdout
        assert "id-1" in result.stdout


# ---------------------------------------------------------------------------
# route
# ---------------------------------------------------------------------------


class TestRouteCli:
    def test_add_sends_action(self) -> None:
        payload = _envelope({"pattern": "*/ads", "removed": 0, "count": 1})
        with patch(
            "agentcloak.client.DaemonClient._send_sync", return_value=payload
        ) as m:
            result = runner.invoke(app, ["route", "add", "*/ads", "--action", "abort"])
        assert result.exit_code == 0, result.stdout
        body = m.call_args.kwargs["json_body"]
        assert body["action"] == "abort"
        assert body["pattern"] == "*/ads"
        assert "added rule" in result.stdout

    def test_remove_all(self) -> None:
        payload = _envelope({"pattern": None, "removed": 3, "count": 0})
        with patch("agentcloak.client.DaemonClient._send_sync", return_value=payload):
            result = runner.invoke(app, ["route", "remove"])
        assert result.exit_code == 0, result.stdout
        assert "removed 3" in result.stdout

    def test_list(self) -> None:
        payload = _envelope(
            {"rules": [{"pattern": "*/ads", "action": "abort"}], "count": 1}
        )
        with patch("agentcloak.client.DaemonClient._send_sync", return_value=payload):
            result = runner.invoke(app, ["route", "list"])
        assert result.exit_code == 0, result.stdout
        assert "abort */ads" in result.stdout


# ---------------------------------------------------------------------------
# emulation headers
# ---------------------------------------------------------------------------


class TestEmulationCli:
    def test_set_headers_parses_pairs(self) -> None:
        payload = _envelope({"headers": {"Authorization": "Bearer t"}, "count": 1})
        with patch(
            "agentcloak.client.DaemonClient._send_sync", return_value=payload
        ) as m:
            result = runner.invoke(
                app, ["emulation", "headers", "-H", "Authorization: Bearer t"]
            )
        assert result.exit_code == 0, result.stdout
        body = m.call_args.kwargs["json_body"]
        assert body["headers"] == {"Authorization": "Bearer t"}
        assert "Authorization" in result.stdout

    def test_no_headers_clears(self) -> None:
        payload = _envelope({"headers": {}, "count": 0})
        with patch(
            "agentcloak.client.DaemonClient._send_sync", return_value=payload
        ) as m:
            result = runner.invoke(app, ["emulation", "headers"])
        assert result.exit_code == 0, result.stdout
        assert m.call_args.kwargs["json_body"] == {"headers": {}}
        assert "cleared" in result.stdout

    def test_malformed_header_errors(self) -> None:
        # No daemon call should happen — parsing fails first.
        with patch("agentcloak.client.DaemonClient._send_sync") as m:
            result = runner.invoke(app, ["emulation", "headers", "-H", "no-colon"])
        assert result.exit_code != 0
        m.assert_not_called()


# ---------------------------------------------------------------------------
# graphql
# ---------------------------------------------------------------------------


class TestGraphqlCli:
    def test_introspect(self) -> None:
        payload = _envelope(
            {
                "status": 200,
                "data": {"__schema": {"types": []}},
                "errors": None,
                "raw": "",
            }
        )
        with patch("agentcloak.client.DaemonClient._send_sync", return_value=payload):
            result = runner.invoke(app, ["graphql", "introspect", "https://x/graphql"])
        assert result.exit_code == 0, result.stdout
        assert "status=200" in result.stdout

    def test_query_with_variables(self) -> None:
        payload = _envelope(
            {"status": 200, "data": {"ok": True}, "errors": None, "raw": ""}
        )
        with patch(
            "agentcloak.client.DaemonClient._send_sync", return_value=payload
        ) as m:
            result = runner.invoke(
                app,
                [
                    "graphql",
                    "query",
                    "https://x/graphql",
                    "{ping}",
                    "--variables",
                    '{"id": 1}',
                ],
            )
        assert result.exit_code == 0, result.stdout
        body = m.call_args.kwargs["json_body"]
        assert body["variables"] == {"id": 1}

    def test_query_invalid_variables_errors(self) -> None:
        with patch("agentcloak.client.DaemonClient._send_sync") as m:
            result = runner.invoke(
                app,
                ["graphql", "query", "https://x/graphql", "{x}", "--variables", "{bad"],
            )
        assert result.exit_code != 0
        m.assert_not_called()
