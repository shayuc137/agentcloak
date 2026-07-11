"""CLI cookie export snapshots and restore composition."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import orjson
from typer.testing import CliRunner

from agentcloak.cli.app import app
from agentcloak.core.config import Paths

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


def _export_envelope() -> dict[str, object]:
    return {
        "ok": True,
        "seq": 4,
        "data": {
            "cookies": [{"name": "sid", "value": "abc"}],
            "count": 1,
        },
    }


def test_export_without_output_keeps_stdout_and_writes_profile_snapshot(
    tmp_path: Path,
) -> None:
    client = MagicMock()
    client.cookies_export_sync.return_value = _export_envelope()
    client.health_sync.return_value = {"ok": True, "active_profile": "dos"}
    paths = Paths(root=tmp_path)

    with (
        patch("agentcloak.cli.commands.cookies_cmd.DaemonClient", return_value=client),
        patch(
            "agentcloak.cli.commands.cookies_cmd.load_config",
            return_value=(paths, MagicMock()),
        ),
    ):
        result = runner.invoke(app, ["cookies", "export"])

    assert result.exit_code == 0, result.output
    assert "sid=abc" in result.stdout
    snapshot = tmp_path / "profiles" / "dos" / "cookies-snapshot.json"
    assert orjson.loads(snapshot.read_bytes()) == _export_envelope()["data"]


def test_restore_reads_default_snapshot_and_calls_import(tmp_path: Path) -> None:
    snapshot = tmp_path / "profiles" / "dos" / "cookies-snapshot.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_bytes(orjson.dumps(_export_envelope()["data"]))
    client = MagicMock()
    client.health_sync.return_value = {"ok": True, "active_profile": "dos"}
    client._send_sync.return_value = {
        "ok": True,
        "seq": 5,
        "data": {"imported": 1},
    }
    paths = Paths(root=tmp_path)

    with (
        patch("agentcloak.cli.commands.cookies_cmd.DaemonClient", return_value=client),
        patch(
            "agentcloak.cli.commands.cookies_cmd.load_config",
            return_value=(paths, MagicMock()),
        ),
    ):
        result = runner.invoke(app, ["cookies", "restore"])

    assert result.exit_code == 0, result.output
    assert "imported 1 cookies" in result.stdout
    client._send_sync.assert_called_once_with(
        "POST",
        "/cookies/import",
        json_body={"cookies": [{"name": "sid", "value": "abc"}]},
        params=None,
    )


def test_restore_file_override_skips_health(tmp_path: Path) -> None:
    snapshot = tmp_path / "manual.json"
    snapshot.write_bytes(orjson.dumps([{"name": "sid", "value": "manual"}]))
    client = MagicMock()
    client._send_sync.return_value = {
        "ok": True,
        "seq": 5,
        "data": {"imported": 1},
    }

    with patch("agentcloak.cli.commands.cookies_cmd.DaemonClient", return_value=client):
        result = runner.invoke(app, ["cookies", "restore", "--file", str(snapshot)])

    assert result.exit_code == 0, result.output
    client.health_sync.assert_not_called()


def test_restore_missing_snapshot_reports_export_recovery(tmp_path: Path) -> None:
    client = MagicMock()
    client.health_sync.return_value = {"ok": True, "active_profile": None}
    paths = Paths(root=tmp_path)

    with (
        patch("agentcloak.cli.commands.cookies_cmd.DaemonClient", return_value=client),
        patch(
            "agentcloak.cli.commands.cookies_cmd.load_config",
            return_value=(paths, MagicMock()),
        ),
    ):
        result = runner.invoke(app, ["cookies", "restore"])

    assert result.exit_code == 1
    assert "does not exist" in result.output
    assert "run cookies export first" in result.output
    client._send_sync.assert_not_called()
