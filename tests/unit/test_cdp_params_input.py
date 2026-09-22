"""Validate CLI parameter sources before any daemon connection is created."""

import io
import json
import sys
from unittest.mock import patch

import pytest

from agentcloak.cli import output
from agentcloak.cli.app import main


@pytest.fixture(autouse=True)
def reset_output_mode():
    yield
    output.set_json_mode(enabled=False)
    output.set_pretty(enabled=False)


@pytest.mark.parametrize("source", ["default", "inline", "file", "stdin"])
def test_parameter_sources_preserve_json(source, tmp_path, monkeypatch, capsys):
    params = (
        {}
        if source == "default"
        else {"expression": "'你好\\nworld'", "returnByValue": True}
    )
    encoded = json.dumps(params, ensure_ascii=False).encode()
    path = tmp_path / "parameters.json"
    path.write_bytes(encoded)
    arguments = {
        "default": [],
        "inline": ["--params", encoded.decode()],
        "file": ["--params-file", str(path)],
        "stdin": ["--params-file", "-"],
    }[source]
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(encoded)))
    monkeypatch.setattr(
        sys, "argv", ["cloak", "cdp", "send", "Runtime.evaluate", *arguments, "--json"]
    )
    with patch(
        "agentcloak.client.DaemonClient._send_sync",
        return_value={"ok": True, "data": {}},
    ) as send:
        main()
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert send.call_args.kwargs["json_body"] == {
        "method": "Runtime.evaluate",
        "params": params,
        "timeout": 30000,
    }


@pytest.mark.parametrize(
    "case",
    [
        "conflict",
        "missing",
        "directory",
        "empty",
        "json",
        "array",
        "null",
        "utf8",
        "stdin-empty",
        "stdin-utf8",
    ],
)
def test_invalid_input_never_contacts_daemon(case, tmp_path, monkeypatch, capsys):
    path = tmp_path / "parameters.json"
    data = {
        "empty": b"",
        "json": b"{",
        "array": b"[]",
        "null": b"null",
        "utf8": b'"\xff"',
    }.get(case, b"{}")
    if case == "directory":
        path.mkdir()
    elif case != "missing":
        path.write_bytes(data)
    arguments = ["--params-file", str(path)]
    if case == "conflict":
        arguments += ["--params", "{}"]
    if case.startswith("stdin-"):
        arguments = ["--params-file", "-"]
        monkeypatch.setattr(
            sys,
            "stdin",
            io.TextIOWrapper(io.BytesIO(b"" if case == "stdin-empty" else b"\xff")),
        )
    monkeypatch.setattr(
        sys, "argv", ["cloak", "cdp", "send", "Runtime.evaluate", *arguments, "--json"]
    )
    with patch("agentcloak.cli.commands.cdp.DaemonClient") as client:
        with pytest.raises(SystemExit) as caught:
            main()
        client.assert_not_called()
    assert caught.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_argument"
    assert "--params" in result["error"]["message"]
