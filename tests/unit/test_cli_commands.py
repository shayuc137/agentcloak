"""CLI command end-to-end tests via CliRunner.

These tests exercise the CLI dispatch path through ``typer.testing.CliRunner``
with the daemon HTTP layer mocked at ``DaemonClient._send_sync`` /
``DaemonClient.screenshot_sync`` / ``DaemonClient.health_sync``. The mocks
return canned envelopes shaped like real daemon responses; the test then
asserts on:

* text-mode output (default since v0.3.0) — the renderer in
  :mod:`agentcloak.core.text_renderers` formats the inner ``data`` dict
* ``--json`` mode — the full envelope is echoed verbatim

The goal is regression coverage for the renderer dispatch in
:mod:`agentcloak.cli._dispatch`. Step 3 of v0.3.x rewrote the
``dispatch_text_or_json`` helper and the per-command renderer wiring, but
only ``doctor`` and ``spell`` had CliRunner tests — every other command's
text path was untested.

Mock approach
-------------
Each test class patches ``agentcloak.client.DaemonClient`` at the
``_send_sync`` method (most commands) or at the specialised typed methods
(screenshot uses ``screenshot_sync``, daemon status uses ``health_sync``).
This avoids spinning up a real daemon or hitting the network. We don't
patch ``_send_async`` because the CLI is sync-only.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from agentcloak.cli import output as cli_output
from agentcloak.cli.app import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_cli_mode() -> Any:
    """Reset module-level json/pretty flags between tests.

    Typer's ``CliRunner`` re-invokes the root callback, but the flags live as
    module globals in :mod:`agentcloak.cli.output` — once a ``--json`` test
    flips ``_json_mode`` to ``True`` every subsequent invocation (including
    text-mode ones) sees it as ``True`` until something resets it. Without
    this fixture the test order would matter, which is exactly the kind of
    flaky-test trap the suite must avoid (PRD: "无 flaky test").
    """
    cli_output.set_json_mode(enabled=False)
    cli_output.set_pretty(enabled=False)
    yield
    cli_output.set_json_mode(enabled=False)
    cli_output.set_pretty(enabled=False)


def _envelope(data: dict[str, Any], *, seq: int = 1) -> dict[str, Any]:
    """Canonical daemon success envelope."""
    return {"ok": True, "seq": seq, "data": data}


# ---------------------------------------------------------------------------
# B1: navigate
# ---------------------------------------------------------------------------


class TestNavigate:
    """``cloak navigate <url>`` — text: ``url | title``."""

    def test_navigate_text_mode(self) -> None:
        payload = _envelope(
            {"url": "https://example.com/", "title": "Example Domain"}, seq=3
        )
        with patch("agentcloak.client.DaemonClient._send_sync", return_value=payload):
            result = runner.invoke(app, ["navigate", "https://example.com/"])
        assert result.exit_code == 0, result.stdout
        # Renderer should produce ``url | title`` exactly.
        assert "https://example.com/ | Example Domain" in result.stdout

    def test_navigate_json_mode(self) -> None:
        payload = _envelope(
            {"url": "https://example.com/", "title": "Example Domain"}, seq=7
        )
        with patch("agentcloak.client.DaemonClient._send_sync", return_value=payload):
            result = runner.invoke(app, ["--json", "navigate", "https://example.com/"])
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["ok"] is True
        assert data["seq"] == 7
        assert data["data"]["url"] == "https://example.com/"
        assert data["data"]["title"] == "Example Domain"


# ---------------------------------------------------------------------------
# B1: snapshot
# ---------------------------------------------------------------------------


class TestSnapshot:
    """``cloak snapshot`` — text: header line + tree."""

    def _snap_payload(self) -> dict[str, Any]:
        return _envelope(
            {
                "url": "https://example.com/",
                "title": "Example",
                "tree_text": "[1] button 'OK'\n[2] link 'Home'",
                "total_nodes": 2,
                "total_interactive": 2,
            },
            seq=4,
        )

    def test_snapshot_text_mode_emits_header_and_tree(self) -> None:
        with patch(
            "agentcloak.client.DaemonClient._send_sync",
            return_value=self._snap_payload(),
        ):
            result = runner.invoke(app, ["snapshot"])
        assert result.exit_code == 0, result.stdout
        # Header has ``# title | url | N nodes (M interactive) | seq=K``.
        assert "# Example | https://example.com/" in result.stdout
        assert "2 nodes" in result.stdout
        assert "2 interactive" in result.stdout
        assert "seq=4" in result.stdout
        # Tree lines must follow the header.
        assert "[1] button 'OK'" in result.stdout
        assert "[2] link 'Home'" in result.stdout

    def test_snapshot_json_mode(self) -> None:
        with patch(
            "agentcloak.client.DaemonClient._send_sync",
            return_value=self._snap_payload(),
        ):
            result = runner.invoke(app, ["--json", "snapshot"])
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["ok"] is True
        assert data["data"]["tree_text"].startswith("[1] button")


# ---------------------------------------------------------------------------
# B1: click
# ---------------------------------------------------------------------------


class TestClick:
    """``cloak click N`` — text: ``clicked [N]``."""

    def test_click_text_mode(self) -> None:
        payload = _envelope({"clicked": True}, seq=5)
        with patch("agentcloak.client.DaemonClient._send_sync", return_value=payload):
            result = runner.invoke(app, ["click", "1"])
        assert result.exit_code == 0, result.stdout
        # The action renderer takes ``kind`` + ``target`` from the CLI side
        # via _action_renderer closure — the daemon JSON no longer carries
        # ``kind``/``target``.
        assert "clicked [1]" in result.stdout

    def test_click_json_mode(self) -> None:
        payload = _envelope({"clicked": True}, seq=11)
        with patch("agentcloak.client.DaemonClient._send_sync", return_value=payload):
            result = runner.invoke(app, ["--json", "click", "1"])
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["ok"] is True
        assert data["seq"] == 11
        assert data["data"]["clicked"] is True


# ---------------------------------------------------------------------------
# B1: tab list
# ---------------------------------------------------------------------------


class TestTabList:
    """``cloak tab list`` — text: ``* 0  url | title``."""

    def _tabs_payload(self) -> dict[str, Any]:
        return _envelope(
            {
                "tabs": [
                    {
                        "tab_id": 0,
                        "url": "https://example.com/",
                        "title": "Example",
                        "active": True,
                    },
                    {
                        "tab_id": 1,
                        "url": "https://github.com/",
                        "title": "GitHub",
                        "active": False,
                    },
                ]
            },
            seq=2,
        )

    def test_tab_list_text_mode_active_tab_marked(self) -> None:
        with patch(
            "agentcloak.client.DaemonClient._send_sync",
            return_value=self._tabs_payload(),
        ):
            result = runner.invoke(app, ["tab", "list"])
        assert result.exit_code == 0, result.stdout
        # Active tab marker is ``*``; inactive has a leading space.
        assert "* 0  https://example.com/" in result.stdout
        # The inactive line should still appear with the URL/title.
        assert "1  https://github.com/" in result.stdout
        # Title joined via ``  | `` per render_tab_list_text.
        assert "GitHub" in result.stdout

    def test_tab_list_json_mode(self) -> None:
        with patch(
            "agentcloak.client.DaemonClient._send_sync",
            return_value=self._tabs_payload(),
        ):
            result = runner.invoke(app, ["--json", "tab", "list"])
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert len(data["data"]["tabs"]) == 2

    def test_tab_list_empty_text_mode(self) -> None:
        empty = _envelope({"tabs": []})
        with patch("agentcloak.client.DaemonClient._send_sync", return_value=empty):
            result = runner.invoke(app, ["tab", "list"])
        assert result.exit_code == 0, result.stdout
        assert "no open tabs" in result.stdout


# ---------------------------------------------------------------------------
# B1: daemon status
# ---------------------------------------------------------------------------


class TestDaemonStatus:
    """``cloak daemon status`` — text: rendered health line."""

    def _health_payload(self) -> dict[str, Any]:
        # /health is *not* wrapped in OkEnvelope — it returns a flat dict
        # (see route handler comment). The CLI command strips ``ok`` from
        # the response before feeding it to render_health_text.
        return {
            "ok": True,
            "seq": 9,
            "stealth_tier": "cloak",
            "browser_ready": True,
            "current_url": "https://example.com/",
            "capture_recording": False,
        }

    def test_daemon_status_text_mode(self) -> None:
        with patch(
            "agentcloak.client.DaemonClient.health_sync",
            return_value=self._health_payload(),
        ):
            result = runner.invoke(app, ["daemon", "status"])
        assert result.exit_code == 0, result.stdout
        # render_health_text joins parts with ``|``.
        assert "tier: cloak" in result.stdout
        assert "browser: ready" in result.stdout
        assert "seq: 9" in result.stdout
        assert "url: https://example.com/" in result.stdout

    def test_daemon_status_json_mode(self) -> None:
        with patch(
            "agentcloak.client.DaemonClient.health_sync",
            return_value=self._health_payload(),
        ):
            result = runner.invoke(app, ["--json", "daemon", "status"])
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["ok"] is True
        # The ``ok`` flag is stripped from the inner payload before
        # re-wrapping (see daemon_cmd.daemon_status).
        assert "ok" not in data["data"]
        assert data["data"]["stealth_tier"] == "cloak"
        assert data["data"]["browser_ready"] is True


# ---------------------------------------------------------------------------
# B1: config list
# ---------------------------------------------------------------------------


class TestConfigList:
    """``cloak config list`` — text: config dump with sources.

    The config command reads from disk via ``load_config()``; it never
    talks to the daemon. We patch ``load_config`` to return a known shape
    so the test stays hermetic.
    """

    def test_config_list_text_mode(self, tmp_path: Any) -> None:
        from agentcloak.core.config import AgentcloakConfig, Paths

        cfg = AgentcloakConfig()
        paths = Paths(root=tmp_path)
        with patch(
            "agentcloak.cli.commands.config_cmd.load_config",
            return_value=(paths, cfg),
        ):
            result = runner.invoke(app, ["config", "list"])
        assert result.exit_code == 0, result.stdout
        # Header is the config file path (rendered as ``# /path``).
        assert f"# {paths.config_file}" in result.stdout
        # Dotted key names so users can copy-paste into config get/set.
        assert "daemon.host" in result.stdout
        assert "daemon.port" in result.stdout
        # Sources go in trailing brackets.
        assert "[default]" in result.stdout

    def test_config_list_json_mode(self, tmp_path: Any) -> None:
        from agentcloak.core.config import AgentcloakConfig, Paths

        cfg = AgentcloakConfig()
        paths = Paths(root=tmp_path)
        with patch(
            "agentcloak.cli.commands.config_cmd.load_config",
            return_value=(paths, cfg),
        ):
            result = runner.invoke(app, ["--json", "config", "list"])
        assert result.exit_code == 0, result.stdout
        data = json.loads(result.stdout)
        assert data["ok"] is True
        fields = data["data"]["fields"]
        assert "daemon.host" in fields
        # Each field is ``{"value": ..., "source": ...}``.
        assert fields["daemon.host"]["value"] == "127.0.0.1"
