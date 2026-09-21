"""Sequential JSONL calls through one CLI process and HTTP connection pool."""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path  # noqa: TC003 (Typer resolves annotations at runtime)
from typing import Any, Literal

import orjson
import typer
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentcloak.cli.output import is_json_mode, value
from agentcloak.client import DaemonClient
from agentcloak.core.errors import AgentBrowserError

app = typer.Typer(invoke_without_command=True)


class BatchCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(pattern=r"^/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$")
    params: dict[str, str | int | float | bool] | None = None
    body: dict[str, Any] | None = None


@app.callback(invoke_without_command=True)
def request_batch(
    calls_file: Path | None = typer.Option(
        None, "--calls-file", help="JSONL file; omit or use '-' to read stdin."
    ),
) -> None:
    """Execute mixed daemon calls in order; stop at the first failure.

    Each JSONL object contains method, path, optional params and body.
    --json emits one indexed envelope per input record, flushed immediately.
    Earlier successful calls are not rolled back. Session/workspace stay fixed.
    """
    try:
        source = (
            contextlib.nullcontext(sys.stdin)
            if calls_file is None or str(calls_file) == "-"
            else calls_file.open(encoding="utf-8")
        )
    except OSError as exc:
        from agentcloak.core.input import invalid_input

        raise invalid_input(f"Cannot read batch file: {exc}") from exc
    client = DaemonClient()
    index = 0
    with source as stream, client.connection():
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                call = BatchCall.model_validate_json(line)
                params = {
                    key: str(item).lower() if isinstance(item, bool) else str(item)
                    for key, item in (call.params or {}).items()
                }
                result = client._send_sync(  # pyright: ignore[reportPrivateUsage]
                    call.method, call.path, params=params, json_body=call.body
                )
            except (ValidationError, AgentBrowserError) as exc:
                code = (
                    exc.error
                    if isinstance(exc, AgentBrowserError)
                    else "invalid_argument"
                )
                message = exc.hint if isinstance(exc, AgentBrowserError) else str(exc)
                result = {"ok": False, "error": {"code": code, "message": message}}
            if "ok" not in result:
                result = {"ok": True, "seq": 0, "data": result}
            record = {"index": index, "line": line_number, **result}
            if is_json_mode():
                value(orjson.dumps(record).decode())
            else:
                value(f"{index}: " + orjson.dumps(result).decode())
            if not result.get("ok", False):
                raise SystemExit(1)
            index += 1
