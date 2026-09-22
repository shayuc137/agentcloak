"""Evaluation chooses document identity, not execution-context arrival order."""

import asyncio
import json
import os
import sys

from .test_recovery_evidence import evaluate, navigate
from .test_recovery_evidence import private_api as private_api


async def test_main_world_owns_reads_and_writes(private_api, local_server):
    client, _ = private_api
    await navigate(client, local_server, page="evaluate-frames.html")
    ready = await client.post(
        "/wait",
        json={
            "condition": "js",
            "value": "frames.length === 2 && "
            "Array.from(document.querySelectorAll('iframe'))"
            ".every(f=>f.contentDocument.readyState === 'complete')",
        },
    )
    assert ready.is_success, ready.text
    assert (await evaluate(client, "({owner, isTop:window===top})"))["result"] == {
        "owner": "root",
        "isTop": True,
    }
    assert (await evaluate(client, "++window.writes"))["result"] == 1
    values = (
        await evaluate(
            client,
            "[writes,frames[0].writes,frames[1].writes,frames[0].frames[0].writes]",
        )
    )["result"]
    assert values == [1, 0, 0, 0]

    focused = await client.post("/frame/focus", json={"name": "second"})
    assert focused.is_success, focused.text
    assert (await evaluate(client, "owner"))["result"] == "root"
    shot = await client.get("/screenshot")
    assert shot.json()["data"]["title"] == "Evaluation owner"
    await client.post("/frame/focus", json={"main": True})
    for _ in range(3):
        await evaluate(
            client,
            "new Promise(r=>{const f=document.querySelector('iframe');"
            "f.onload=r;f.src=f.src})",
        )
        assert (await evaluate(client, "owner"))["result"] == "root"
    await evaluate(client, "document.querySelectorAll('iframe').forEach(f=>f.remove())")
    assert (await evaluate(client, "owner"))["result"] == "root"
    cross_origin = (
        local_server.replace("127.0.0.1", "localhost") + "/evaluate-child.html"
    )
    await evaluate(
        client,
        "new Promise(r=>{const f=document.createElement('iframe');"
        "f.onload=()=>r(true);f.src="
        + json.dumps(cross_origin)
        + ";document.body.append(f)})",
    )
    assert (await evaluate(client, "[owner, frames.length]"))["result"] == ["root", 1]
    for _ in range(3):
        await navigate(client, local_server, page="evaluate-frames.html")
        assert (await evaluate(client, "owner"))["result"] == "root"
    opened = await client.post(
        "/tab/new", json={"url": f"{local_server}/evaluate-child.html"}
    )
    assert opened.is_success, opened.text
    assert (await evaluate(client, "owner"))["result"] == "child"
    await client.post("/tab/switch", json={"tab_id": 0})
    assert (await evaluate(client, "owner"))["result"] == "root"


async def test_navigation_does_not_replay_evaluation(private_api, local_server):
    client, _ = private_api
    await navigate(client, local_server, page="evaluate-frames.html")
    response = await client.post(
        "/evaluate",
        json={
            "js": "sessionStorage.setItem('evalAttempts',"
            "String(+(sessionStorage.getItem('evalAttempts')||0)+1));"
            "setTimeout(()=>location.reload(),0); new Promise(()=>{})"
        },
    )
    assert response.is_error, response.text
    assert response.json()["error"] == "evaluate_failed"
    ready = await client.post("/wait", json={"condition": "selector", "value": "#root"})
    assert ready.is_success, ready.text
    assert (await evaluate(client, "sessionStorage.getItem('evalAttempts')"))[
        "result"
    ] == "1"
    assert (await evaluate(client, "owner"))["result"] == "root"


async def test_cli_and_mcp_evaluate_their_own_page(private_api, local_server, tmp_path):
    from mcp.server.fastmcp import FastMCP

    from agentcloak.client import DaemonClient
    from agentcloak.mcp.tools import content

    http, _ = private_api
    (tmp_path / "config.toml").write_text(f"[daemon]\nport={http.base_url.port}\n")
    runner = tmp_path / "cli.py"
    runner.write_text(
        "import sys\nfrom pathlib import Path\n"
        "import agentcloak.core.config as config\n"
        "root = Path(sys.argv.pop(1))\nconfig._default_root = lambda: root\n"
        "from agentcloak.cli.app import main\nmain()\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTCLOAK_")}

    async def cli(*args):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(runner),
            str(tmp_path),
            "--session",
            "cli-eval",
            "--json",
            *args,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await process.communicate()
        assert process.returncode == 0, (out, err)
        return json.loads(out)["data"]

    await cli("navigate", f"{local_server}/evaluate-frames.html")
    assert (await cli("js", "evaluate", "owner"))["result"] == "root"
    client = DaemonClient(
        host="127.0.0.1",
        port=http.base_url.port,
        session_id="mcp-eval",
        auto_start=False,
    )
    mcp = FastMCP("evaluate-context")
    content.register(mcp, client)
    await client.navigate(f"{local_server}/evaluate-child.html")
    result = await mcp.call_tool("agentcloak_evaluate", {"js": "owner"})
    assert "child" in str(result)
    assert (await cli("js", "evaluate", "owner"))["result"] == "root"
