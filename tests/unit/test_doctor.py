"""Tests for cli/commands/doctor.py — JSON output format and fix mode."""

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agentcloak.cli import output as cli_output
from agentcloak.cli.app import app
from agentcloak.core.text_renderers import (
    render_doctor_detail_text,
    render_doctor_text,
)
from agentcloak.daemon.services.diagnostic_service import (
    DiagnosticService,
    _detect_linux_distro,
)

runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_cli_mode() -> Any:
    """Reset module-level json/pretty flags between tests.

    ``cli.output`` keeps the ``--json`` toggle in a module global; once any
    test flips it the next text-mode invocation still sees JSON output unless
    we reset. Without the fixture test order silently determines outcomes.
    """
    cli_output.set_json_mode(enabled=False)
    cli_output.set_pretty(enabled=False)
    yield
    cli_output.set_json_mode(enabled=False)
    cli_output.set_pretty(enabled=False)


class TestDoctorCommand:
    # CLI defaults to text output; tests assert against the legacy JSON
    # envelope via ``--json`` (still the contract for scripts and MCP).
    def test_outputs_valid_json(self) -> None:
        result = runner.invoke(app, ["--json", "doctor"])
        data = json.loads(result.stdout)
        assert "ok" in data
        assert data["ok"] is True
        assert "data" in data

    def test_has_checks_array(self) -> None:
        result = runner.invoke(app, ["--json", "doctor"])
        data = json.loads(result.stdout)
        checks = data["data"]["checks"]
        assert isinstance(checks, list)
        assert len(checks) > 0

    def test_each_check_has_required_fields(self) -> None:
        result = runner.invoke(app, ["--json", "doctor"])
        data = json.loads(result.stdout)
        for check in data["data"]["checks"]:
            assert "name" in check
            assert "ok" in check
            assert "detail" in check
            assert "hint" in check

    def test_python_version_check_passes(self) -> None:
        result = runner.invoke(app, ["--json", "doctor"])
        data = json.loads(result.stdout)
        checks = data["data"]["checks"]
        py_check = next(c for c in checks if c["name"] == "python_version")
        assert py_check["ok"] is True

    def test_has_seq_field(self) -> None:
        result = runner.invoke(app, ["--json", "doctor"])
        data = json.loads(result.stdout)
        assert "seq" in data
        assert data["seq"] == 0

    def test_path_entry_check_present(self) -> None:
        # ``path_entry`` warns when ``agentcloak``/``cloak`` aren't on PATH.
        # In CI/dev runs the venv's scripts dir is always on PATH, so this is
        # a smoke test confirming the check is wired up.
        result = runner.invoke(app, ["--json", "doctor"])
        data = json.loads(result.stdout)
        names = {c["name"] for c in data["data"]["checks"]}
        assert "path_entry" in names

    def test_playwright_libs_check_present(self) -> None:
        result = runner.invoke(app, ["--json", "doctor"])
        data = json.loads(result.stdout)
        names = {c["name"] for c in data["data"]["checks"]}
        assert "playwright_libs" in names

    def test_fix_flag_returns_fix_section(self) -> None:
        # ``--fix`` adds a ``fix`` dict to the response (actions, command,
        # executed). On an already-healthy environment ``command`` is empty.
        result = runner.invoke(app, ["--json", "doctor", "--fix"])
        data = json.loads(result.stdout)
        assert "fix" in data["data"]
        assert "actions" in data["data"]["fix"]
        assert "command" in data["data"]["fix"]
        assert "executed" in data["data"]["fix"]
        # No daemon running in test env → not healthy, but the fix dict
        # itself is still present.

    def test_fix_help_advertises_sudo(self) -> None:
        result = runner.invoke(app, ["doctor", "--help"])
        assert "--fix" in result.stdout
        assert "--sudo" in result.stdout

    def test_detail_help_present(self) -> None:
        # ``--detail`` is the new escape hatch for the legacy verbose layout
        # — preflight relies on its presence so older scripts keep working.
        result = runner.invoke(app, ["doctor", "--help"])
        assert "--detail" in result.stdout

    def test_detail_outputs_per_check_lines(self) -> None:
        # Sanity: the detail mode keeps the legacy per-check layout so users
        # debugging individual probes still get the same view they had pre-
        # v0.3.x. Each check shows up as a single ``[level] name | detail``
        # line; the python_version probe is always present so we anchor on it.
        result = runner.invoke(app, ["doctor", "--detail"])
        assert "[ok] python_version" in result.stdout
        # Concise mode would only print a single "all N checks passed" line —
        # the detail view has many lines.
        assert result.stdout.count("\n") > 5


class TestDoctorTextRenderers:
    """Pure-function tests for the doctor renderers — no CLI dispatch."""

    def test_concise_all_pass_with_runtime(self) -> None:
        # All checks green + daemon up: two-line summary with the runtime
        # status describing browser/headless/humanize/proxy/profile.
        data = {
            "healthy": True,
            "checks": [
                {"name": "python_version", "ok": True, "detail": "3.12", "hint": ""},
                {"name": "daemon", "ok": True, "detail": "...", "hint": ""},
            ],
            "runtime": {
                "daemon_ok": True,
                "browser_description": "CloakBrowser 0.3.27",
                "headless": True,
                "humanize": True,
                "proxy": "",
                "active_profile": "",
            },
        }
        out = render_doctor_text(data)
        lines = out.split("\n")
        assert len(lines) == 2
        assert lines[0].startswith("all 2 checks passed | agentcloak ")
        expected_status = (
            "CloakBrowser 0.3.27 | headless | humanize "
            "| no proxy | no profile (ephemeral)"
        )
        assert lines[1] == expected_status

    def test_concise_humanize_off_omits_humanize_token(self) -> None:
        # Humanize disabled: the status line drops the ``humanize`` segment
        # rather than rendering ``no humanize`` — matches the PRD example.
        data = {
            "healthy": True,
            "checks": [{"name": "x", "ok": True, "detail": "", "hint": ""}],
            "runtime": {
                "daemon_ok": True,
                "browser_description": "CloakBrowser 0.3.27",
                "headless": True,
                "humanize": False,
                "proxy": "",
                "active_profile": "",
            },
        }
        out = render_doctor_text(data)
        assert "humanize" not in out.split("\n")[1]
        assert "headless" in out.split("\n")[1]

    def test_concise_failure_only_lists_failed_checks(self) -> None:
        # Failures: header reports M/N, then one line per failure, then the
        # runtime status. Successful checks must not appear.
        data = {
            "healthy": False,
            "checks": [
                {"name": "python_version", "ok": True, "detail": "3.12", "hint": ""},
                {
                    "name": "cloakbrowser_binary",
                    "ok": False,
                    "detail": "not found",
                    "hint": "run 'cloak doctor --fix'",
                },
            ],
            "runtime": {
                "daemon_ok": True,
                "browser_description": "CloakBrowser 0.3.27",
                "headless": True,
                "humanize": False,
                "proxy": "",
                "active_profile": "",
            },
        }
        out = render_doctor_text(data)
        lines = out.split("\n")
        # Header + 1 failure line + status line = 3 lines, regardless of how
        # many checks passed.
        assert lines[0] == "1/2 checks passed"
        expected_fail = (
            "[fail] cloakbrowser_binary | not found | run 'cloak doctor --fix'"
        )
        assert expected_fail in out
        assert "[ok]" not in out
        assert "python_version" not in out

    def test_concise_daemon_down_replaces_status_line(self) -> None:
        # No daemon → can't introspect browser/proxy/profile, so the second
        # line tells the user the daemon isn't running rather than a stale
        # default-rendered status.
        data = {
            "healthy": True,
            "checks": [{"name": "x", "ok": True, "detail": "", "hint": ""}],
            "runtime": {"daemon_ok": False},
        }
        out = render_doctor_text(data)
        lines = out.split("\n")
        assert lines[1] == "daemon not running (auto-starts on first command)"

    def test_concise_profile_set(self) -> None:
        # Active profile renders ``profile: <name>`` rather than the
        # ephemeral fallback.
        data = {
            "healthy": True,
            "checks": [{"name": "x", "ok": True, "detail": "", "hint": ""}],
            "runtime": {
                "daemon_ok": True,
                "browser_description": "CloakBrowser 0.3.27",
                "headless": True,
                "humanize": False,
                "proxy": "",
                "active_profile": "github",
            },
        }
        out = render_doctor_text(data)
        assert "profile: github" in out
        assert "ephemeral" not in out

    def test_concise_proxy_set(self) -> None:
        # Proxy renders verbatim — the renderer doesn't interpret the URL.
        data = {
            "healthy": True,
            "checks": [{"name": "x", "ok": True, "detail": "", "hint": ""}],
            "runtime": {
                "daemon_ok": True,
                "browser_description": "CloakBrowser 0.3.27",
                "headless": True,
                "humanize": True,
                "proxy": "socks5://host:1080",
                "active_profile": "",
            },
        }
        out = render_doctor_text(data)
        assert "socks5://host:1080" in out
        assert "no proxy" not in out

    def test_detail_renderer_lists_every_check(self) -> None:
        # The detail renderer is the backward-compat path — every check
        # produces exactly one line.
        data = {
            "checks": [
                {"name": "a", "ok": True, "detail": "1", "hint": ""},
                {"name": "b", "ok": False, "detail": "2", "hint": "fix it"},
                {"name": "c", "ok": True, "level": "info", "detail": "3", "hint": "x"},
            ]
        }
        out = render_doctor_detail_text(data)
        lines = out.split("\n")
        assert len(lines) == 3
        assert lines[0] == "[ok] a | 1"
        assert lines[1] == "[fail] b | 2 | hint: fix it"
        assert lines[2] == "[info] c | 3 | hint: x"


class TestDiagnosticServiceDirect:
    """Direct calls into DiagnosticService — exercises code paths the CLI
    layer doesn't easily reach without mocking the whole filesystem."""

    def test_doctor_smoke(self) -> None:
        ds = DiagnosticService()
        with tempfile.TemporaryDirectory() as td:
            report = ds.doctor(data_dir=Path(td))
        assert "healthy" in report
        assert "checks" in report
        assert "extras" in report
        # extras structure: ``available`` is True when no extras run or all pass.
        assert "available" in report["extras"]
        assert "checks" in report["extras"]

    def test_doctor_fix_creates_data_dir(self) -> None:
        ds = DiagnosticService()
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "nested" / "agentcloak"
            assert not target.exists()
            report = ds.doctor_fix(data_dir=target, execute_sudo=False)
            # Assertions must run inside the ``with`` block — the temp dir
            # gets recursively deleted when we leave the context, which would
            # make every ``.exists()`` check trivially fail.
            assert target.exists()
            assert (target / "profiles").exists()
            assert (target / "logs").exists()
            actions = report["fix"]["actions"]
            names = {a["name"] for a in actions}
            assert "data_directory" in names

    def test_distro_detection_returns_tuple(self) -> None:
        # Just confirm the function returns the expected 3-tuple shape and
        # the falls-back-to-debian behaviour doesn't crash on unknown distros.
        name, mgr_argv, pkg = _detect_linux_distro()
        assert isinstance(name, str) and name
        assert isinstance(mgr_argv, list) and mgr_argv
        assert isinstance(pkg, str) and pkg
