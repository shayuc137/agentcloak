"""CLI and MCP environment controls preserve explicit false and DPR values."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from agentcloak.cli.app import app
from agentcloak.core.errors import AgentBrowserError
from agentcloak.core.input import validate_dpr


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_density(value):
    with pytest.raises(AgentBrowserError):
        validate_dpr(value)


@pytest.mark.parametrize(
    "args,expected",
    [
        (
            [
                "emulate",
                "--color-scheme",
                "dark",
                "--no-reduced-motion",
                "--pointer",
                "fine",
            ],
            {"color_scheme": "dark", "reduced_motion": False, "pointer": "fine"},
        ),
        (["emulate", "reset"], {"reset": True}),
        (["emulate"], {}),
        (
            ["viewport", "set", "600x400", "--dpr", "2"],
            {"width": 600, "height": 400, "dpr": 2},
        ),
    ],
)
def test_cli_environment_controls(args, expected):
    client = MagicMock()
    client._send_sync.return_value = {"ok": True, "data": expected}
    with (
        patch("agentcloak.cli.commands.emulate.DaemonClient", return_value=client),
        patch("agentcloak.cli.commands.viewport.DaemonClient", return_value=client),
    ):
        result = CliRunner().invoke(app, ["--json", *args])
    assert result.exit_code == 0, result.output
    assert client._send_sync.call_args.kwargs["json_body"] == expected


async def test_mcp_environment_controls():
    from mcp.server.fastmcp import FastMCP

    from agentcloak.mcp.tools.emulation import register

    client = AsyncMock()
    client.emulate.return_value = {
        "ok": True,
        "data": {"color_scheme": "dark", "reduced_motion": False, "pointer": None},
    }
    client.viewport.return_value = {
        "ok": True,
        "data": {"width": 600, "height": 400, "dpr": 2},
    }
    mcp = FastMCP("test")
    register(mcp, client)
    result = await mcp._tool_manager._tools["agentcloak_emulate"].fn(
        color_scheme="dark", reduced_motion=False
    )
    assert "dark" in result and "False" in result
    client.emulate.assert_awaited_once_with(
        color_scheme="dark", reduced_motion=False, pointer=None, reset=False
    )
    result = await mcp._tool_manager._tools["agentcloak_viewport"].fn(
        width=600, height=400, dpr=2
    )
    assert "dpr=2" in result
    client.viewport.assert_awaited_once_with(width=600, height=400, dpr=2)
