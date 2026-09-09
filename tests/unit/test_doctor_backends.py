"""Backend-specific doctor regressions; all downloads are mocked."""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from typer.testing import CliRunner

from agentcloak.cli import output as cli_output
from agentcloak.cli.app import app
from agentcloak.core.config import AgentcloakConfig, Paths
from agentcloak.daemon.services.diagnostic_service import DiagnosticService


@pytest.fixture
def diagnostic(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTCLOAK_TIER", raising=False)
    monkeypatch.delenv("AGENTCLOAK_DEFAULT_TIER", raising=False)
    monkeypatch.delenv("AGENTCLOAK_HEADLESS", raising=False)
    monkeypatch.delenv("CLOAKBROWSER_BINARY_PATH", raising=False)
    service = DiagnosticService()
    monkeypatch.setattr(
        service,
        "_check_package",
        lambda name: {"name": name, "ok": True, "detail": "installed", "hint": ""},
    )
    for method, name in [
        ("_check_path_entry", "path_entry"),
        ("_check_playwright_libs", "playwright_libs"),
        ("_check_stale_chromium", "stale_chromium"),
    ]:
        monkeypatch.setattr(
            service,
            method,
            Mock(
                return_value={
                    "name": name,
                    "ok": True,
                    "detail": "available",
                    "hint": "",
                }
            ),
        )
    return service


def configure(root: Path, tier: str, *, headless: bool = True) -> None:
    (root / "config.toml").write_text(
        f'[browser]\ndefault_tier = "{tier}"\nheadless = {str(headless).lower()}\n'
    )


@pytest.mark.parametrize("tier", ["auto", "cloak"])
def test_cloak_install_is_healthy_without_system_chromium(diagnostic, tmp_path, tier):
    configure(tmp_path, tier)
    with (
        patch("shutil.which", return_value=None),
        patch(
            "cloakbrowser.binary_info",
            return_value={
                "installed": True,
                "binary_path": "/cache/Chromium.app/Contents/MacOS/Chromium",
            },
        ),
        patch("cloakbrowser.ensure_binary") as download,
    ):
        report = diagnostic.doctor(data_dir=tmp_path)
    assert report["healthy"] is True
    assert "chromium" not in {c["name"] for c in report["checks"]}
    download.assert_not_called()


def test_missing_cloak_binary_still_fails_with_system_chrome(diagnostic, tmp_path):
    with (
        patch("shutil.which", return_value="/usr/bin/google-chrome"),
        patch(
            "cloakbrowser.binary_info",
            return_value={
                "installed": False,
                "binary_path": "/cache/missing/chrome",
            },
        ),
    ):
        report = diagnostic.doctor(data_dir=tmp_path)
    assert report["healthy"] is False
    failed = [c for c in report["checks"] if not c["ok"]]
    assert [c["name"] for c in failed] == ["cloakbrowser_binary"]
    assert "doctor --fix" in failed[0]["hint"]


@pytest.mark.parametrize("state", ["file", "missing", "directory"])
def test_cloak_override_takes_priority(diagnostic, tmp_path, monkeypatch, state):
    override = tmp_path / "custom-chrome"
    if state == "file":
        override.touch()
    elif state == "directory":
        override.mkdir()
    monkeypatch.setenv("CLOAKBROWSER_BINARY_PATH", str(override))
    with patch("cloakbrowser.binary_info") as info:
        check = diagnostic._check_cloakbrowser_binary()
    assert check["ok"] is (state == "file")
    assert check["detail"] == str(override)
    info.assert_not_called()


def test_cloak_cache_override_uses_current_build(diagnostic, tmp_path, monkeypatch):
    from cloakbrowser.config import get_binary_path

    monkeypatch.setenv("CLOAKBROWSER_CACHE_DIR", str(tmp_path / "custom-cache"))
    binary = get_binary_path()
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.touch()
    with patch("cloakbrowser.ensure_binary") as download:
        check = diagnostic._check_cloakbrowser_binary()
    assert check["ok"] is True
    assert Path(check["detail"]) == binary
    download.assert_not_called()


@pytest.mark.parametrize("headless", [True, False])
def test_playwright_checks_selected_mode_only(diagnostic, tmp_path, headless):
    configure(tmp_path, "playwright", headless=headless)
    with (
        patch(
            "agentcloak.browser.binaries.find_playwright_chromium",
            return_value="/pw-cache/chrome",
        ) as find,
        patch("cloakbrowser.binary_info") as info,
        patch("cloakbrowser.ensure_binary") as download,
    ):
        report = diagnostic.doctor_fix(data_dir=tmp_path)
    assert report["healthy"] is True
    assert all(call.kwargs == {"headless": headless} for call in find.call_args_list)
    assert find.called
    info.assert_not_called()
    download.assert_not_called()


def test_remote_bridge_skips_local_browser_requirements(diagnostic, tmp_path):
    configure(tmp_path, "remote_bridge", headless=False)
    with (
        patch("platform.system", return_value="Linux"),
        patch("shutil.which", return_value=None),
        patch("cloakbrowser.binary_info") as info,
        patch("cloakbrowser.ensure_binary") as download,
    ):
        report = diagnostic.doctor_fix(data_dir=tmp_path)
    assert report["healthy"] is True
    assert report["fix"]["command"] == ""
    assert "xvfb" not in {c["name"] for c in report["extras"]["checks"]}
    diagnostic._check_playwright_libs.assert_not_called()
    info.assert_not_called()
    download.assert_not_called()


def test_cloak_fix_rechecks_downloaded_binary(diagnostic, tmp_path):
    with (
        patch("shutil.which", return_value=None),
        patch(
            "cloakbrowser.binary_info",
            side_effect=[
                {"installed": False, "binary_path": "/cache/chrome"},
                {"installed": True, "binary_path": "/cache/chrome"},
            ],
        ),
        patch("cloakbrowser.ensure_binary", return_value="/cache/chrome") as download,
    ):
        report = diagnostic.doctor_fix(data_dir=tmp_path)
    assert report["healthy"] is True
    assert report["fix"]["before_ok"] is False
    assert report["fix"]["actions"][0]["fixed"] is True
    download.assert_called_once()


def test_cli_exits_successfully_for_cached_cloak(diagnostic, tmp_path):
    from agentcloak.cli.commands import doctor

    cli_output.set_json_mode(enabled=False)
    try:
        with (
            patch.object(
                doctor,
                "load_config",
                return_value=(Paths(tmp_path), AgentcloakConfig()),
            ),
            patch.object(doctor, "DiagnosticService", return_value=diagnostic),
            patch.object(
                doctor,
                "_probe_daemon_runtime",
                return_value=(
                    {"name": "daemon", "ok": True, "detail": "", "hint": ""},
                    {"daemon_ok": False},
                ),
            ),
            patch("shutil.which", return_value=None),
            patch(
                "cloakbrowser.binary_info",
                return_value={
                    "installed": True,
                    "binary_path": "/cache/chrome",
                },
            ),
            patch("cloakbrowser.ensure_binary") as download,
        ):
            result = CliRunner().invoke(app, ["--json", "doctor"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["data"]["healthy"] is True
        download.assert_not_called()
    finally:
        cli_output.set_json_mode(enabled=False)


@pytest.mark.parametrize("outcome", [None, RuntimeError("driver unavailable")])
def test_playwright_missing_or_failed_probe_is_actionable(outcome):
    with patch(
        "agentcloak.browser.binaries.find_playwright_chromium",
        return_value=None,
        side_effect=outcome,
    ):
        check = DiagnosticService._check_chromium()
    assert check["ok"] is False
    assert "playwright install chromium" in check["hint"]
