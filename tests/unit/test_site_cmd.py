"""Tests for CLI spell commands and spell discovery."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from agentcloak.cli import output as cli_output
from agentcloak.cli.app import app
from agentcloak.spells.registry import get_registry

runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_cli_mode() -> Any:
    cli_output.set_json_mode(enabled=False)
    cli_output.set_pretty(enabled=False)
    yield
    cli_output.set_json_mode(enabled=False)
    cli_output.set_pretty(enabled=False)


def _parse_json(stdout: str) -> dict:
    """Extract the JSON object from CLI output, skipping any log lines."""
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise ValueError(f"No JSON found in output: {stdout!r}")


class TestSpellList:
    def setup_method(self) -> None:
        get_registry().clear()

    def test_list_empty(self) -> None:
        # ``--json`` keeps the envelope shape these assertions rely on; the
        # default CLI output is now plain text since v0.3.0.
        result = runner.invoke(app, ["--json", "spell", "list"])
        assert result.exit_code == 0
        data = _parse_json(result.stdout)
        assert data["ok"] is True
        assert data["data"]["count"] >= 0

    def test_list_after_discovery(self) -> None:
        from agentcloak.spells.discovery import discover_spells

        discover_spells()
        result = runner.invoke(app, ["--json", "spell", "list"])
        assert result.exit_code == 0
        data = _parse_json(result.stdout)
        assert data["data"]["count"] >= 2


class TestSpellInfo:
    def setup_method(self) -> None:
        get_registry().clear()
        from agentcloak.spells.discovery import discover_spells

        discover_spells()

    def test_info_existing(self) -> None:
        result = runner.invoke(app, ["--json", "spell", "info", "httpbin/headers"])
        assert result.exit_code == 0
        data = _parse_json(result.stdout)
        assert data["ok"] is True
        assert data["data"]["site"] == "httpbin"
        assert data["data"]["name"] == "headers"
        assert data["data"]["strategy"] == "public"

    def test_info_missing(self) -> None:
        result = runner.invoke(app, ["spell", "info", "nonexist/cmd"])
        assert result.exit_code == 1

    def test_info_bad_format(self) -> None:
        result = runner.invoke(app, ["spell", "info", "noSlash"])
        assert result.exit_code == 1


class TestSpellRun:
    def setup_method(self) -> None:
        get_registry().clear()
        from agentcloak.spells.discovery import discover_spells

        discover_spells()

    def test_run_browser_required_spell_routes_to_daemon(self) -> None:
        payload = {
            "ok": True,
            "seq": 3,
            "data": {"result": [{"title": "Example"}]},
        }
        with patch(
            "agentcloak.client.DaemonClient._send_sync", return_value=payload
        ) as send:
            result = runner.invoke(app, ["spell", "run", "example/title"])

        assert result.exit_code == 0, result.output
        assert '"title": "Example"' in result.stdout
        send.assert_called_once_with(
            "POST",
            "/spell/run",
            json_body={"name": "example/title", "args": {}},
            params=None,
        )


class TestDiscovery:
    def setup_method(self) -> None:
        get_registry().clear()

    def test_discover_builtin(self) -> None:
        from agentcloak.spells.discovery import discover_spells

        counts = discover_spells()
        assert counts["builtin"] >= 2
        assert counts["total"] >= 2

    def test_discover_idempotent(self) -> None:
        from agentcloak.spells.discovery import discover_spells

        discover_spells()
        count_first = len(get_registry())
        discover_spells()
        assert len(get_registry()) == count_first
