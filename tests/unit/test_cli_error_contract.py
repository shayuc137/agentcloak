"""Exercise the installed entrypoint path, including Click parsing failures."""

from __future__ import annotations

import json
import sys
from unittest.mock import patch

import httpx
import pytest

from agentcloak.cli import output
from agentcloak.cli.app import main
from agentcloak.client import DaemonClient
from agentcloak.core.errors import AgentBrowserError


@pytest.fixture(autouse=True)
def reset_output_mode():
    yield
    output.set_json_mode(enabled=False)
    output.set_pretty(enabled=False)


@pytest.mark.parametrize(
    "args, code",
    [
        (["missing-command"], "invalid_request"),
        (["--session"], "invalid_request"),
        (["config", "get", "nonexistent.key"], "config_error"),
        (["navigate"], "invalid_request"),
        (["js", "evaluate"], "command_failed"),
        (["js", "evaluate", "1", "--unknown"], "invalid_request"),
        (["js", "evaluate", "--file", "/nonexistent/probe.js"], "command_failed"),
    ],
)
def test_real_entrypoint_json_failures(args, code, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["cloak", *args, "--json"])
    with pytest.raises(SystemExit) as caught:
        main()
    output = capsys.readouterr()
    assert caught.value.code != 0
    envelope = json.loads(output.out)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == code
    assert envelope["error"]["message"]
    assert code in output.err


@pytest.mark.parametrize("json_mode", [False, True])
def test_real_entrypoint_evaluation_failure(json_mode, monkeypatch, capsys):
    args = ["cloak", "js", "evaluate", "throw new Error('probe')"]
    monkeypatch.setattr(sys, "argv", args + (["--json"] if json_mode else []))
    exc = AgentBrowserError(
        error="evaluate_failed", hint="Error: probe", action="fix JS"
    )
    with patch.object(DaemonClient, "_send_sync", side_effect=exc):
        with pytest.raises(SystemExit) as caught:
            main()
    output = capsys.readouterr()
    assert caught.value.code == 1
    assert "evaluate_failed" in output.err
    assert "Error: probe" in output.err
    if json_mode:
        assert json.loads(output.out)["error"] == {
            "code": "evaluate_failed",
            "message": "Error: probe",
        }
    else:
        assert output.out == ""


@pytest.mark.parametrize(
    "status, payload, code",
    [
        (500, {}, "daemon_request_failed"),
        (200, {"ok": False}, "daemon_request_failed"),
        (
            400,
            {"ok": False, "error": {"code": "evaluate_failed", "message": "boom"}},
            "evaluate_failed",
        ),
        (200, [], "daemon_invalid_response"),
    ],
)
def test_client_rejects_failed_and_invalid_responses(status, payload, code):
    client = DaemonClient(host="127.0.0.1", port=19999, auto_start=False)
    response = httpx.Response(status, json=payload)
    with pytest.raises(AgentBrowserError) as caught:
        client._parse_response(response)
    assert caught.value.error == code


def test_bridge_diagnostic_failure_emits_one_json_error(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["cloak", "bridge", "doctor", "--json"])
    with patch.object(
        DaemonClient,
        "health_sync",
        return_value={"ok": True, "data": {"remote_connected": False}},
    ):
        with pytest.raises(SystemExit) as caught:
            main()
    captured = capsys.readouterr()
    assert caught.value.code == 1
    result = json.loads(captured.out)
    assert result["ok"] is False
    assert result["error"]["code"] == "bridge_check_failed"
    assert result["data"]["healthy"] is False


def test_skill_uninstall_permission_failure_is_not_success(monkeypatch, capsys):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    target = MagicMock()
    target.is_symlink.return_value = True
    target.readlink.side_effect = PermissionError("permission denied")
    monkeypatch.setattr(sys, "argv", ["cloak", "skill", "uninstall", "--json"])
    with patch(
        "agentcloak.cli.commands.skill_cmd._all_platforms",
        return_value=[SimpleNamespace(target=target)],
    ):
        with pytest.raises(SystemExit) as caught:
            main()
    captured = capsys.readouterr()
    assert caught.value.code == 1
    result = json.loads(captured.out)
    assert result["ok"] is False
    assert result["error"]["code"] == "skill_uninstall_failed"
    assert "permission denied" in result["error"]["message"]
