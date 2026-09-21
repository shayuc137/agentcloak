"""CLI processes against a private daemon, browser and discovery directory."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import sys
from pathlib import Path

import httpx
import pytest


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.parametrize("backend", ["playwright", "cloak"])
async def test_cli_namespaces_recovery_and_read_only_discovery(
    tmp_path, local_server, backend
):
    root = tmp_path / "state"
    root.mkdir()
    config_port, runtime_port = free_port(), free_port()
    while config_port == runtime_port:
        runtime_port = free_port()
    (root / "config.toml").write_text(
        f"[daemon]\nport={config_port}\nsession_idle_timeout=0\n"
        f'[browser]\ndefault_tier="{backend}"\nheadless=true\n'
        'humanize=false\nisolation="workspace"\n'
    )
    # Isolate state and disable optional LAN advertising; entrypoints stay real.
    runner = tmp_path / "entry.py"
    runner.write_text(
        "import asyncio, sys\nfrom pathlib import Path\n"
        "sys.modules['zeroconf'] = None\n"
        "import agentcloak.core.config as config\n"
        "root = Path(sys.argv.pop(1))\nconfig._default_root = lambda: root\n"
        "if sys.argv[1] == 'serve':\n"
        "    from agentcloak.daemon.server import start\n"
        "    asyncio.run(start(port=int(sys.argv[2]), profile='integration', "
        "log_level='info'))\n"
        "else:\n    from agentcloak.cli.app import main\n    main()\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTCLOAK_")}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    log_path = tmp_path / "daemon.log"
    log = log_path.open("w")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(runner),
        str(root),
        "serve",
        str(runtime_port),
        env=env,
        stdout=log,
        stderr=log,
    )
    base = f"http://127.0.0.1:{runtime_port}"
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()

    async def cli(cwd, *args, success=True, text=False):
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(runner),
            str(root),
            *args,
            *([] if text else ["--json"]),
            "--session=same",
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), 20)
        except BaseException:
            proc.kill()
            await proc.wait()
            raise
        if success:
            assert proc.returncode == 0, (
                stdout.decode(),
                stderr.decode(),
                log_path.read_text(),
            )
        return stdout.decode().strip() if text else json.loads(stdout)

    try:
        async with httpx.AsyncClient(base_url=base, timeout=1) as client:
            last_status = "no response"
            for _ in range(200):
                if process.returncode is not None:
                    pytest.fail(log_path.read_text())
                try:
                    response = await client.get("/health")
                    last_status = f"{response.status_code}: {response.text}"
                    if response.is_success:
                        break
                except httpx.HTTPError as exc:
                    last_status = str(exc)
                await asyncio.sleep(0.05)
            else:
                pytest.fail(
                    "Private daemon did not start: "
                    + last_status
                    + log_path.read_text()
                )

        if os.name != "nt":
            os.kill(process.pid, signal.SIGUSR1)
            for _ in range(100):
                if "asyncio_task_dump" in log_path.read_text():
                    break
                await asyncio.sleep(0.01)
            assert "asyncio_task_dump" in log_path.read_text()

        record = root / "daemon.json"
        data = json.loads(record.read_bytes())
        assert data["port"] == runtime_port != config_port
        data["pid"] = 2147483647
        record.write_text(json.dumps(data))
        original = record.read_bytes()
        record.chmod(0o444)
        root.chmod(0o555)
        try:
            await cli(first, "daemon", "status")
            assert record.read_bytes() == original
        finally:
            root.chmod(0o700)
            record.chmod(0o600)

        await asyncio.gather(
            cli(first, "navigate", f"{local_server}/index.html"),
            cli(second, "navigate", f"{local_server}/form.html"),
        )
        sessions = (await cli(first, "session", "list", "--all"))["data"]["sessions"]
        assert {item["label"] for item in sessions} >= {"one", "two"}
        assert {item["workspace_path"] for item in sessions} >= {
            str(first),
            str(second),
        }
        popup = await cli(first, "js", "evaluate", "window.open('/form.html'); true")
        popup_id = popup["data"]["new_tab"]["tab_id"]
        closed = await cli(first, "tab", "close", "--others", text=True)
        assert closed == f"closed 1 tab | ids: {popup_id}"
        assert (await cli(first, "tab", "list"))["data"]["count"] == 1
        assert (
            await cli(first, "tab", "close", "--others", text=True) == "closed 0 tabs"
        )
        shot = await cli(
            first,
            "screenshot",
            "--format",
            "png",
            "--expect-url",
            "*/index.html",
            "-o",
            str(tmp_path / "evidence.png"),
        )
        assert shot["data"]["url"].endswith("/index.html")
        assert shot["data"]["pixel_width"] > 0
        mismatch = await cli(
            first,
            "navigate",
            f"{local_server}/index.html",
            "--expect-path",
            "/login",
            success=False,
        )
        assert mismatch["ok"] is False
        assert mismatch["error"]["code"] == "url_mismatch"
        endpoint = await cli(first, "cdp", "endpoint", "--page")
        assert "/devtools/page/" in endpoint["data"]["ws_endpoint"]
        await cli(
            first, "js", "evaluate", "localStorage.setItem('cli-workspace','first')"
        )
        await cli(
            second, "js", "evaluate", "localStorage.setItem('cli-workspace','second')"
        )
        a, b = await asyncio.gather(
            cli(first, "js", "evaluate", "localStorage.getItem('cli-workspace')"),
            cli(second, "js", "evaluate", "localStorage.getItem('cli-workspace')"),
        )
        assert a["data"]["result"] == "first"
        assert b["data"]["result"] == "second"
        await cli(
            first, "js", "evaluate", "document.cookie='workspace-cookie=first;Path=/'"
        )
        await cli(
            second, "js", "evaluate", "document.cookie='workspace-cookie=second;Path=/'"
        )
        await cli(first, "cookies", "export")
        await cli(second, "cookies", "export")
        await cli(first, "cookies", "clear")
        await cli(first, "cookies", "restore")
        assert (await cli(first, "js", "evaluate", "document.cookie"))["data"][
            "result"
        ] == "workspace-cookie=first"
        assert (await cli(second, "js", "evaluate", "document.cookie"))["data"][
            "result"
        ] == "workspace-cookie=second"
        await cli(first, "session", "close")
        assert (await cli(second, "js", "evaluate", "location.pathname"))["data"][
            "result"
        ] == "/form.html"
        await cli(second, "session", "close")
        await cli(first, "navigate", f"{local_server}/index.html")
        assert (
            await cli(first, "js", "evaluate", "localStorage.getItem('cli-workspace')")
        )["data"]["result"] == "first"

        await cli(
            first, "cdp", "send", "Browser.close", "--timeout", "1000", success=False
        )
        await cli(second, "navigate", f"{local_server}/form.html")
        assert (
            await cli(second, "js", "evaluate", "localStorage.getItem('cli-workspace')")
        )["data"]["result"] == "second"
        await cli(first, "navigate", f"{local_server}/index.html")
        assert (
            await cli(first, "js", "evaluate", "localStorage.getItem('cli-workspace')")
        )["data"]["result"] == "first"
        # Exercise production daemon shutdown with active, unsaved workspace state.
        await cli(
            first,
            "js",
            "evaluate",
            "localStorage.setItem('cli-workspace','restart-first')",
        )
        await cli(
            second,
            "js",
            "evaluate",
            "localStorage.setItem('cli-workspace','restart-second')",
        )
        async with httpx.AsyncClient(base_url=base, timeout=2) as client:
            await client.post("/shutdown")
        await asyncio.wait_for(process.wait(), 15)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(runner),
            str(root),
            "serve",
            str(runtime_port),
            env=env,
            stdout=log,
            stderr=log,
        )
        async with httpx.AsyncClient(base_url=base, timeout=1) as client:
            for _ in range(200):
                try:
                    if (await client.get("/health")).is_success:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.05)
            else:
                pytest.fail("Restart failed: " + log_path.read_text())
        for directory, expected in [
            (first, "restart-first"),
            (second, "restart-second"),
        ]:
            await cli(directory, "navigate", f"{local_server}/index.html")
            assert (
                await cli(
                    directory, "js", "evaluate", "localStorage.getItem('cli-workspace')"
                )
            )["data"]["result"] == expected

    finally:
        root.chmod(0o700)
        if process.returncode is None:
            try:
                async with httpx.AsyncClient(base_url=base, timeout=2) as client:
                    await client.post("/shutdown")
            except httpx.HTTPError:
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 15)
            except TimeoutError:
                process.kill()
                await process.wait()
        log.close()
