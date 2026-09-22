"""Discover actual daemon instances over multicast, including a fallback port."""

import asyncio
import json
import socket
import time

import pytest

from .test_daemon_ownership import isolated_env, ready, spawn, stop
from .test_daemon_ownership import launcher as launcher


async def test_ready_daemons_advertise_bound_ports_and_withdraw(tmp_path, launcher):
    zeroconf = pytest.importorskip("zeroconf")
    from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf

    service_type = "_agentcloak._tcp.local."
    observer = AsyncZeroconf(ip_version=zeroconf.IPVersion.V4Only)
    present = set()
    removed = set()

    def changed(zeroconf, service_type, name, state_change):
        if state_change == zeroconf_module.ServiceStateChange.Removed:
            removed.add(name)
        else:
            present.add(name)

    zeroconf_module = zeroconf
    browser = AsyncServiceBrowser(observer.zeroconf, service_type, handlers=[changed])
    processes = []
    names = []
    try:
        with socket.socket() as occupied:
            env, base_port = isolated_env(tmp_path / "first")
            occupied.bind(("127.0.0.1", base_port))
            occupied.listen()
            env["AGENTCLOAK_HOST"] = "0.0.0.0"
            start = time.monotonic()
            first = await spawn(launcher, env)
            processes.append(first)
            await ready(first, base_port + 1)
            assert time.monotonic() - start < 5
            record = json.loads((tmp_path / "first" / "daemon.json").read_bytes())
            assert record["port"] == base_port + 1
            other_env, other_port = isolated_env(tmp_path / "second")
            other_env["AGENTCLOAK_HOST"] = "0.0.0.0"
            second = await spawn(launcher, other_env)
            processes.append(second)
            await ready(second, other_port)
            for port in (base_port + 1, other_port):
                async with asyncio.timeout(10):
                    while True:
                        matches = []
                        for name in list(present):
                            info = await observer.async_get_service_info(
                                service_type, name, timeout=300
                            )
                            if info is not None and info.port == port:
                                matches.append(info)
                        if matches:
                            assert len(matches) == 1
                            assert b"token" not in matches[0].properties
                            names.append(matches[0].name)
                            break
                        await asyncio.sleep(0.05)
            assert names[0] != names[1]
            for process, port in ((first, base_port + 1), (second, other_port)):
                await stop(process, port)
            async with asyncio.timeout(10):
                while not set(names).issubset(removed):
                    await asyncio.sleep(0.05)
    finally:
        for process in processes:
            if process.returncode is None:
                process.kill()
            await process.communicate()
        await browser.async_cancel()
        await observer.async_close()
