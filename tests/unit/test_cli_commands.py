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

import base64
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from agentcloak.cli import output as cli_output
from agentcloak.cli.app import app

if TYPE_CHECKING:
    from pathlib import Path

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
# B1: evaluate
# ---------------------------------------------------------------------------


class TestEvaluate:
    """``cloak js evaluate`` — inline, preset, and UTF-8 file sources."""

    def test_evaluate_file_preserves_multiline_code(self, tmp_path: Path) -> None:
        script = "const label = 'DOS';\nlabel + `-${location.pathname}`;\n"
        script_path = tmp_path / "probe.js"
        script_path.write_text(script, encoding="utf-8")
        payload = _envelope(
            {"result": "DOS-/ui-lab", "truncated": False, "total_size": 13}
        )

        with patch(
            "agentcloak.client.DaemonClient._send_sync", return_value=payload
        ) as send:
            result = runner.invoke(app, ["js", "evaluate", "--file", str(script_path)])

        assert result.exit_code == 0, result.output
        assert "DOS-/ui-lab" in result.stdout
        assert send.call_args.kwargs["json_body"] == {
            "world": "main",
            "js": script,
        }

    def test_evaluate_rejects_multiple_sources(self, tmp_path: Path) -> None:
        script_path = tmp_path / "probe.js"
        script_path.write_text("1 + 1", encoding="utf-8")

        result = runner.invoke(
            app, ["js", "evaluate", "2 + 2", "--file", str(script_path)]
        )

        assert result.exit_code == 1
        assert "multiple JavaScript sources" in result.output

    def test_evaluate_rejects_missing_source(self) -> None:
        result = runner.invoke(app, ["js", "evaluate"])

        assert result.exit_code == 1
        assert "no JavaScript source" in result.output

    def test_evaluate_rejects_non_utf8_file(self, tmp_path: Path) -> None:
        script_path = tmp_path / "probe.js"
        script_path.write_bytes(b"\xff\xfe")

        result = runner.invoke(app, ["js", "evaluate", "--file", str(script_path)])

        assert result.exit_code == 1
        assert "cannot read JavaScript file" in result.output

    def test_evaluate_preset_remains_supported(self) -> None:
        payload = _envelope({"result": {}, "truncated": False, "total_size": 2})
        with patch(
            "agentcloak.client.DaemonClient._send_sync", return_value=payload
        ) as send:
            result = runner.invoke(app, ["js", "evaluate", "--preset", "react_inspect"])

        assert result.exit_code == 0, result.output
        assert send.call_args.kwargs["json_body"] == {
            "world": "main",
            "preset": "react_inspect",
        }


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

    def test_snapshot_within_forwards_selector(self) -> None:
        with patch(
            "agentcloak.client.DaemonClient._send_sync",
            return_value=self._snap_payload(),
        ) as send:
            result = runner.invoke(app, ["snapshot", "--within", "main"])

        assert result.exit_code == 0, result.output
        assert send.call_args.kwargs["params"]["selector"] == "main"


class TestScreenshot:
    def test_uses_response_format_for_default_extension_and_forwards_wait(
        self, tmp_path: Path
    ) -> None:
        payload = _envelope(
            {
                "base64": base64.b64encode(b"png-bytes").decode(),
                "size": 9,
                "format": "png",
            }
        )
        with (
            patch(
                "agentcloak.client.DaemonClient.screenshot_sync",
                return_value=payload,
            ) as screenshot,
            patch(
                "agentcloak.cli.commands.browser.gettempdir",
                return_value=str(tmp_path),
            ),
            patch("agentcloak.cli.commands.browser.time.time", return_value=1.0),
        ):
            result = runner.invoke(
                app,
                [
                    "screenshot",
                    "--wait-selector",
                    "#ready",
                    "--wait-timeout",
                    "1200",
                ],
            )

        assert result.exit_code == 0, result.output
        output = tmp_path / "agentcloak-1000.png"
        assert result.stdout.strip() == str(output)
        assert output.read_bytes() == b"png-bytes"
        screenshot.assert_called_once_with(
            full_page=False,
            format=None,
            quality=None,
            wait_selector="#ready",
            wait_timeout=1200,
        )


class TestScreenshotDiff:
    @staticmethod
    def _write_png(path: Path, pixels: list[tuple[int, int, int, int]]) -> bytes:
        from io import BytesIO

        from PIL import Image

        image = Image.new("RGBA", (2, 2))
        image.putdata(pixels)
        image.save(path)
        stream = BytesIO()
        image.save(stream, format="PNG")
        return stream.getvalue()

    def test_local_comparison_does_not_create_daemon_client(
        self, tmp_path: Path
    ) -> None:
        baseline = tmp_path / "baseline.png"
        current = tmp_path / "current.png"
        pixels = [(0, 0, 0, 255)] * 4
        self._write_png(baseline, pixels)
        self._write_png(current, pixels)

        with patch("agentcloak.cli.commands.diff_cmd.DaemonClient") as client:
            result = runner.invoke(
                app,
                ["diff", "screenshot", str(baseline), "--current", str(current)],
            )

        assert result.exit_code == 0, result.output
        assert result.stdout.strip() == (
            "diff 0/4 pixels (0%) | max_delta=0 | 2x2 | threshold=0"
        )
        client.assert_not_called()

    def test_json_reports_paths_and_exact_metrics(self, tmp_path: Path) -> None:
        baseline = tmp_path / "baseline.png"
        current = tmp_path / "current.png"
        self._write_png(baseline, [(0, 0, 0, 255)] * 4)
        self._write_png(
            current,
            [(20, 0, 0, 255), (0, 0, 0, 255), (0, 0, 0, 255), (0, 0, 0, 255)],
        )

        result = runner.invoke(
            app,
            [
                "--json",
                "diff",
                "screenshot",
                str(baseline),
                "--current",
                str(current),
            ],
        )

        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)["data"]
        assert data["changed_pixels"] == 1
        assert data["difference_percent"] == 25
        assert data["max_channel_delta"] == 20
        assert data["baseline"] == str(baseline.resolve())
        assert data["current"] == str(current.resolve())

    def test_live_comparison_requests_png_and_uses_returned_bytes(
        self, tmp_path: Path
    ) -> None:
        baseline = tmp_path / "baseline.png"
        base_pixels = [(0, 0, 0, 255)] * 4
        self._write_png(baseline, base_pixels)
        current_bytes = self._write_png(
            tmp_path / "actual.png",
            [(50, 0, 0, 255), *base_pixels[1:]],
        )
        payload = _envelope(
            {
                "base64": base64.b64encode(current_bytes).decode(),
                "size": len(current_bytes),
                "format": "png",
            },
            seq=9,
        )

        with patch(
            "agentcloak.client.DaemonClient.screenshot_sync", return_value=payload
        ) as screenshot:
            result = runner.invoke(app, ["--json", "diff", "screenshot", str(baseline)])

        assert result.exit_code == 0, result.output
        envelope = json.loads(result.stdout)
        assert envelope["seq"] == 9
        assert envelope["data"]["changed_pixels"] == 1
        assert envelope["data"]["current"] == "<live-page>"
        screenshot.assert_called_once_with(format="png")

    def test_dimension_mismatch_uses_structured_error(self, tmp_path: Path) -> None:
        from PIL import Image

        baseline = tmp_path / "baseline.png"
        current = tmp_path / "current.png"
        self._write_png(baseline, [(0, 0, 0, 255)] * 4)
        Image.new("RGBA", (3, 1)).save(current)

        result = runner.invoke(
            app,
            ["diff", "screenshot", str(baseline), "--current", str(current)],
        )

        assert result.exit_code == 1
        assert "baseline=2x2, current=3x1" in result.output
        assert "same viewport and dimensions" in result.output


class TestSpellRunRouting:
    @staticmethod
    def _entry(strategy: Any) -> Any:
        from agentcloak.spells.types import SpellEntry, SpellMeta

        return SpellEntry(
            meta=SpellMeta(
                site="dos",
                name="login",
                strategy=strategy,
                description="Log in to DOS",
            )
        )

    @staticmethod
    def _registry(entry: Any) -> MagicMock:
        registry = MagicMock()
        registry.__len__.return_value = 1
        registry.get.return_value = entry
        return registry

    def test_public_spell_stays_local_without_daemon(self) -> None:
        from agentcloak.core.types import Strategy

        entry = self._entry(Strategy.PUBLIC)
        registry = self._registry(entry)
        execute = AsyncMock(return_value=[{"source": "local"}])
        with (
            patch(
                "agentcloak.cli.commands.spell_cmd.get_registry",
                return_value=registry,
            ),
            patch("agentcloak.cli.commands.spell_cmd._execute", execute),
            patch("agentcloak.cli.commands.spell_cmd.DaemonClient") as client,
        ):
            result = runner.invoke(app, ["spell", "run", "dos/login", "tenant=demo"])

        assert result.exit_code == 0, result.output
        assert '"source": "local"' in result.stdout
        execute.assert_awaited_once_with(entry, {"tenant": "demo"})
        client.assert_not_called()

    def test_browser_spell_routes_name_and_parsed_args_to_daemon(self) -> None:
        from agentcloak.core.types import Strategy

        entry = self._entry(Strategy.UI)
        registry = self._registry(entry)
        payload = _envelope({"result": [{"logged_in": True}]}, seq=7)
        execute = AsyncMock()
        with (
            patch(
                "agentcloak.cli.commands.spell_cmd.get_registry",
                return_value=registry,
            ),
            patch("agentcloak.cli.commands.spell_cmd._execute", execute),
            patch(
                "agentcloak.client.DaemonClient._send_sync", return_value=payload
            ) as send,
        ):
            result = runner.invoke(
                app,
                ["spell", "run", "dos/login", "tenant=demo", "remember"],
            )

        assert result.exit_code == 0, result.output
        assert '"logged_in": true' in result.stdout
        send.assert_called_once_with(
            "POST",
            "/spell/run",
            json_body={
                "name": "dos/login",
                "args": {"tenant": "demo", "remember": True},
            },
            params=None,
        )
        execute.assert_not_awaited()

    def test_browser_spell_json_preserves_daemon_envelope(self) -> None:
        from agentcloak.core.types import Strategy

        registry = self._registry(self._entry(Strategy.COOKIE))
        payload = _envelope({"result": [{"user": "shayu"}]}, seq=11)
        with (
            patch(
                "agentcloak.cli.commands.spell_cmd.get_registry",
                return_value=registry,
            ),
            patch("agentcloak.client.DaemonClient._send_sync", return_value=payload),
        ):
            result = runner.invoke(app, ["--json", "spell", "run", "dos/login"])

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == payload

    def test_browser_spell_daemon_failure_is_actionable(self) -> None:
        from agentcloak.core.errors import AgentBrowserError
        from agentcloak.core.types import Strategy

        registry = self._registry(self._entry(Strategy.INTERCEPT))
        failure = AgentBrowserError(
            error="daemon_unreachable",
            hint="Cannot reach the agentcloak daemon",
            action="run 'cloak doctor --fix' and retry",
        )
        with (
            patch(
                "agentcloak.cli.commands.spell_cmd.get_registry",
                return_value=registry,
            ),
            patch("agentcloak.client.DaemonClient._send_sync", side_effect=failure),
        ):
            result = runner.invoke(app, ["spell", "run", "dos/login"])

        assert result.exit_code == 1
        assert "Cannot reach the agentcloak daemon" in result.output
        assert "cloak doctor --fix" in result.output


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
