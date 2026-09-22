"""Real CLI processes must not overwrite another daemon's runtime state."""

import asyncio
import json
import os
import socket
import sys

import httpx
import pytest


@pytest.fixture
def launcher(tmp_path):
    runner = tmp_path / "run_cli.py"
    runner.write_text("from agentcloak.cli.app import main\nmain()\n")
    return runner


def isolated_env(root):
    root.mkdir(exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    (root / "config.toml").write_text(
        f"[daemon]\nport={port}\nidle_timeout_min=0\n"
        '[browser]\ndefault_tier="remote_bridge"\n'
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTCLOAK_")}
    env["AGENTCLOAK_HOME"] = str(root)
    return env, port


async def spawn(launcher, env):
    return await asyncio.create_subprocess_exec(
        sys.executable,
        str(launcher),
        "daemon",
        "start",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def ready(process, port):
    async with asyncio.timeout(15), httpx.AsyncClient(trust_env=False) as client:
        while True:
            if process.returncode is not None:
                out, err = await process.communicate()
                pytest.fail(f"daemon exited: {out!r} {err!r}")
            try:
                response = await client.get(f"http://127.0.0.1:{port}/health")
                if response.is_success and response.json()["ok"]:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.05)


async def stop(process, port):
    if process.returncode is None:
        async with httpx.AsyncClient(trust_env=False) as client:
            await client.post(f"http://127.0.0.1:{port}/shutdown")
        await asyncio.wait_for(process.communicate(), 10)
        assert process.returncode == 0


async def test_daemon_state_ownership_and_crash_recovery(tmp_path, launcher):
    first_root, other_root = tmp_path / "first", tmp_path / "other"
    env, port = isolated_env(first_root)
    other_env, other_port = isolated_env(other_root)
    first = await spawn(launcher, env)
    processes = [first]
    try:
        await ready(first, port)
        files = [
            first_root / name
            for name in ("daemon.json", "daemon.pid", "active-session.json")
        ]
        before = [file.read_bytes() for file in files]
        assert json.loads(before[0])["port"] == port
        duplicate = await spawn(launcher, env)
        processes.append(duplicate)
        out, err = await asyncio.wait_for(duplicate.communicate(), 10)
        assert duplicate.returncode != 0, (out, err)
        assert [file.read_bytes() for file in files] == before
        other = await spawn(launcher, other_env)
        processes.append(other)
        await ready(other, other_port)
        assert (
            json.loads((other_root / "daemon.json").read_bytes())["port"] == other_port
        )
        await stop(other, other_port)
        assert not (other_root / "daemon.json").exists()
        assert [file.read_bytes() for file in files] == before
        first.kill()
        await first.communicate()
        assert files[0].exists()
        restarted = await spawn(launcher, env)
        processes.append(restarted)
        await ready(restarted, port)
        assert json.loads(files[0].read_bytes())["pid"] == restarted.pid
        await stop(restarted, port)
        assert not any(file.exists() for file in files)
    finally:
        for process in processes:
            if process.returncode is None:
                process.kill()
            await process.communicate()
