"""Screen recording control, with local artifact export on stop."""

# pyright: reportUnusedFunction=false

from __future__ import annotations

import base64
from pathlib import Path
from tempfile import gettempdir
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from agentcloak.core.input import invalid_input
from agentcloak.core.text_renderers import render_record_text
from agentcloak.mcp._format import format_call

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient


def register(mcp: FastMCP, client: DaemonClient) -> None:
    @mcp.tool()
    async def agentcloak_record(
        action: Literal["start", "status", "stop"],
        format: Literal["webm", "zip"] = "webm",
        max_frames: int = 600,
        max_seconds: int = 120,
        output_path: str = "",
    ) -> str:
        """Record this session's current tab; WebM needs ffmpeg on the daemon.

        Start pins the tab; navigation stays recorded but tab switches do not
        change the target. Limits stop capture and retain frames for stop/export.
        Stop writes a local artifact at output_path or a temporary destination.
        Session close discards unfinished recordings. ZIP needs no encoder.
        """

        async def operate() -> dict[str, Any]:
            if action == "start":
                return await client.record_start(
                    format=format, max_frames=max_frames, max_seconds=max_seconds
                )
            if action == "status":
                return await client.record_status()
            if output_path and not Path(output_path).expanduser().parent.is_dir():
                raise invalid_input("Recording parent directory does not exist")
            result = await client.record_stop()
            data = result["data"]
            output = (
                Path(output_path).expanduser()
                if output_path
                else Path(gettempdir())
                / f"agentcloak-record-{uuid4().hex[:8]}.{data['format']}"
            )
            output.write_bytes(base64.b64decode(data.pop("base64")))
            data["saved"] = str(output)
            return result

        return await format_call(operate(), render_record_text)
