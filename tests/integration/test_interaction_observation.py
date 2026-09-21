"""Stable targeting, timed gestures and in-flight request observations."""

import asyncio
import time

import pytest

from .test_recovery_evidence import evaluate, navigate
from .test_recovery_evidence import private_api as private_api


async def test_selector_and_find(private_api, local_server):
    client, _ = private_api
    await navigate(client, local_server)
    for body in (
        {"kind": "fill", "selector": "#editor", "text": "current"},
        {"kind": "hover", "selector": "#editor", "offset": "1,1"},
        {"kind": "click", "selector": "#editor"},
    ):
        response = await client.post("/action", json=body)
        assert response.is_success, response.text
        assert response.json()["data"]["selector"] == "#editor"
    assert (await evaluate(client, "editor.value"))["result"] == "current"
    for selector in ("button", "#missing", "["):
        response = await client.post(
            "/action", json={"kind": "click", "selector": selector}
        )
        assert response.is_error, response.text
    response = await client.post(
        "/action", json={"kind": "click", "selector": "#editor", "index": 1}
    )
    assert response.is_error
    response = await client.get(
        "/snapshot", params={"find": "EDITOR", "include_selector_map": True}
    )
    assert response.is_success, response.text
    data = response.json()["data"]
    assert "Editor" in data["tree_text"]
    assert "Drag source" not in data["tree_text"]
    refs = data["selector_map"]
    ref = next(int(k) for k, value in refs.items() if value["role"] == "textbox")
    # Selector actions leave the last published refs usable.
    await client.post("/action", json={"kind": "hover", "selector": "#destination"})
    response = await client.post(
        "/action", json={"kind": "fill", "index": ref, "text": "by-ref"}
    )
    assert response.is_success, response.text
    assert (await evaluate(client, "editor.value"))["result"] == "by-ref"
    missing = await client.get("/snapshot", params={"find": "no such label"})
    assert missing.is_success and missing.json()["data"]["total_interactive"] == 0


async def test_timed_drag_samples_and_failure_release(private_api, local_server):
    client, _ = private_api
    await navigate(client, local_server)
    await evaluate(
        client, "document.addEventListener('pointermove',e=>window.buttons=e.buttons)"
    )
    body = {
        "kind": "drag",
        "from_point": "20,180",
        "to_point": "240,180",
        "steps": 4,
        "hold": 120,
        "duration": 160,
        "sample": "window.pointer",
    }
    started = time.monotonic()
    response = await client.post("/action", json=body)
    assert response.is_success, response.text
    assert time.monotonic() - started >= 0.28
    samples = response.json()["data"]["samples"]
    assert [s["step"] for s in samples] == [1, 2, 3, 4]
    assert samples[-1]["value"] == [240, 180]
    assert samples[0]["elapsed_ms"] >= 120
    failed = await client.post(
        "/action",
        json={**body, "sample": "(() => {throw new Error('sample failed')})()"},
    )
    assert failed.is_error, failed.text
    await client.post("/action", json={"kind": "hover", "at": "250,180"})
    assert (await evaluate(client, "window.buttons"))["result"] == 0


async def test_drag_cancel_releases_mouse(browser_context, local_server):
    ctx = await browser_context.fork_session()
    try:
        await ctx.navigate(f"{local_server}/input-actions.html")
        await ctx.evaluate(
            "document.addEventListener('pointermove',e=>window.buttons=e.buttons)"
        )
        drag = asyncio.create_task(
            ctx.action("drag", "", from_point="20,180", to_point="240,180", hold=10000)
        )
        async with asyncio.timeout(5):
            while not await ctx.evaluate("events.down"):
                await asyncio.sleep(0.01)
        drag.cancel()
        with pytest.raises(asyncio.CancelledError):
            await drag
        await ctx.action("hover", "", at="250,180")
        assert await ctx.evaluate("window.buttons") == 0
    finally:
        await ctx.close()


async def test_pending_filter_and_release(browser_context, local_server):
    from agentcloak.browser.managers.route_manager import RouteRule

    ctx = await browser_context.fork_session()
    sibling = await browser_context.fork_session()
    try:
        await ctx.route_manager.add(RouteRule("network-data.txt", "hold"))
        await ctx.navigate(f"{local_server}/network-controls.html")
        async with asyncio.timeout(5):
            while not ctx.route_manager.pending():
                await asyncio.sleep(0.01)
        entries = await ctx.network(since=0, pending=True, filter="*/network-data.txt")
        assert len(entries) == 1
        assert entries[0]["pending"] and entries[0]["elapsed_ms"] >= 0
        assert await ctx.network(since=0, pending=True, filter="*/missing") == []
        assert await sibling.network(since=0, pending=True) == []
        await ctx.route_manager.remove(None)
        async with asyncio.timeout(5):
            while await ctx.network(since=0, pending=True, filter="*/network-data.txt"):
                await asyncio.sleep(0.01)
        assert len(await ctx.network(since=0, filter="*/network-data.txt")) == 1
    finally:
        await ctx.close()
        await sibling.close()


async def test_cli_and_mcp_targeting(private_api, local_server, tmp_path):
    import json
    import os
    import sys

    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient
    from agentcloak.mcp.tools import interaction, navigation, network, record

    http, _ = private_api
    root = tmp_path / "state"
    root.mkdir()
    (root / "config.toml").write_text(f"[daemon]\nport={http.base_url.port}\n")
    runner = tmp_path / "cli.py"
    runner.write_text(
        "import sys\nfrom pathlib import Path\n"
        "import agentcloak.core.config as config\n"
        "root = Path(sys.argv.pop(1))\nconfig._default_root = lambda: root\n"
        "from agentcloak.cli.app import main\nmain()\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTCLOAK_")}

    async def cli(*args, calls_input=None, success=True):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(runner),
            str(root),
            *args,
            "--json",
            "--session=surface",
            env=env,
            stdin=asyncio.subprocess.PIPE if calls_input is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(calls_input), 15)
        assert (process.returncode == 0) == success, (stdout, stderr)
        if args[0] == "batch":
            return [json.loads(line) for line in stdout.splitlines()]
        return json.loads(stdout)

    await cli("navigate", f"{local_server}/input-actions.html")
    await cli("fill", "--selector", "#editor", "--text", "from-cli")
    await cli("fill", "positional value", "--selector", "#editor")
    found = await cli("snapshot", "--find", "Editor")
    assert "Editor" in found["data"]["tree_text"]
    await cli("network", "--pending", "--filter", "*/missing")
    await cli(
        "drag",
        "--from",
        "20,180",
        "--to",
        "80,180",
        "--hold",
        "10",
        "--duration",
        "20",
        "--steps",
        "2",
        "--sample",
        "1",
    )

    annotated = await cli(
        "screenshot",
        "--annotate",
        "--format",
        "png",
        "-o",
        str(tmp_path / "annotated.png"),
    )
    assert annotated["data"]["annotated"] and annotated["data"]["annotations"]
    await cli("record", "start", "--format", "zip")
    await cli("js", "evaluate", "document.body.style.background='blue'")
    video = await cli("record", "stop", "-o", str(tmp_path / "screen.zip"))
    assert video["data"]["frames"] >= 1 and (tmp_path / "screen.zip").is_file()

    calls = [
        {
            "method": "POST",
            "path": "/action",
            "body": {"kind": "fill", "selector": "#editor", "text": "batched"},
        },
        {"method": "POST", "path": "/evaluate", "body": {"js": "editor.value"}},
        {"method": "GET", "path": "/snapshot", "params": {"find": "Editor"}},
    ]

    def encode(rows):
        return ("\n".join(json.dumps(row) for row in rows) + "\n").encode()

    records = await cli("batch", calls_input=encode(calls))
    assert [r["index"] for r in records] == [0, 1, 2]
    assert records[1]["data"]["result"] == "batched"
    failed = await cli(
        "batch",
        calls_input=encode(
            [
                {
                    "method": "POST",
                    "path": "/evaluate",
                    "body": {"js": "throw new Error('stop here')"},
                },
                {
                    "method": "POST",
                    "path": "/action",
                    "body": {
                        "kind": "fill",
                        "selector": "#editor",
                        "text": "must-not-run",
                    },
                },
            ]
        ),
        success=False,
    )
    assert len(failed) == 1 and not failed[0]["ok"]
    assert (await cli("js", "evaluate", "editor.value"))["data"]["result"] == "batched"
    bad = await cli(
        "batch",
        calls_input=encode([{"method": "GET", "path": "https://example.invalid/"}]),
        success=False,
    )
    assert bad[0]["error"]["code"] == "invalid_argument"

    streaming = await asyncio.create_subprocess_exec(
        sys.executable,
        str(runner),
        str(root),
        "batch",
        "--json",
        "--pretty",
        "--session=surface",
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        streaming.stdin.write(encode([{"method": "GET", "path": "/health"}]))
        await streaming.stdin.drain()
        first = json.loads(await asyncio.wait_for(streaming.stdout.readline(), 5))
        assert first["ok"] and first["index"] == 0
        streaming.stdin.close()
        await asyncio.wait_for(streaming.wait(), 5)
        assert streaming.returncode == 0
    finally:
        if streaming.returncode is None:
            streaming.kill()
            await streaming.wait()

    started = time.monotonic()
    for _ in range(6):
        await cli("js", "evaluate", "1")
    separate_time = time.monotonic() - started
    started = time.monotonic()
    await cli(
        "batch",
        calls_input=encode(
            [{"method": "POST", "path": "/evaluate", "body": {"js": "1"}}] * 6
        ),
    )
    batch_time = time.monotonic() - started
    print(f"six evaluate calls: separate={separate_time:.3f}s batch={batch_time:.3f}s")

    client = DaemonClient(
        host="127.0.0.1",
        port=http.base_url.port,
        session_id="mcp-surface",
        auto_start=False,
    )
    mcp = FastMCP("surfaces")
    for register in (
        navigation.register,
        interaction.register,
        network.register,
        record.register,
    ):
        register(mcp, client)
    await client.navigate(f"{local_server}/input-actions.html")
    await mcp.call_tool(
        "agentcloak_action", {"kind": "fill", "selector": "#editor", "text": "from-mcp"}
    )
    assert (await client.evaluate("editor.value"))["data"]["result"] == "from-mcp"
    await mcp.call_tool("agentcloak_record", {"action": "start", "format": "zip"})
    await client.evaluate("document.body.style.background='green'")
    await asyncio.sleep(0.15)
    await mcp.call_tool(
        "agentcloak_record",
        {"action": "stop", "output_path": str(tmp_path / "mcp.zip")},
    )
    assert (tmp_path / "mcp.zip").is_file()
    annotated = await mcp.call_tool(
        "agentcloak_screenshot", {"annotate": True, "format": "png"}
    )
    assert "image" in str(annotated).lower()
    result = await mcp.call_tool("agentcloak_snapshot", {"find": "Editor"})
    assert "Editor" in str(result)
    await mcp.call_tool("agentcloak_network", {"pending": True, "filter": "*/missing"})


async def test_selector_preserves_content_scan(private_api, local_server):
    client, config = private_api
    config.security.content_scan = True
    config.security.content_scan_patterns = ["UnsafeMarker"]
    await navigate(client, local_server)
    await evaluate(client, "editor.setAttribute('aria-label', 'UnsafeMarker')")
    response = await client.post(
        "/action", json={"kind": "fill", "selector": "#editor", "text": "blocked"}
    )
    assert response.json()["error"] == "content_scan_blocked"
    assert (await evaluate(client, "editor.value"))["result"] == ""
