"""Optional discovery must yield the loop and close partially registered resources."""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentcloak.core import discovery


@pytest.fixture
def fake_discovery(monkeypatch):
    info = SimpleNamespace(name="example", port=0)
    announcements = AsyncMock()
    register = AsyncMock(side_effect=lambda *a, **k: announcements())
    close = AsyncMock()
    zc = SimpleNamespace(async_register_service=register, async_close=close)
    constructed = []

    def service(type_, name, **kwargs):
        info.name, info.port = name, kwargs["port"]
        info.properties = kwargs["properties"]
        return info

    def factory():
        constructed.append(zc)
        return zc

    monkeypatch.setitem(sys.modules, "zeroconf", SimpleNamespace(ServiceInfo=service))
    monkeypatch.setitem(
        sys.modules, "zeroconf.asyncio", SimpleNamespace(AsyncZeroconf=factory)
    )
    return info, zc, announcements, constructed


async def test_registration_yields_and_cancellation_closes(fake_discovery):
    info, zc, announcements, _ = fake_discovery
    announced = asyncio.Event()
    announcements.side_effect = announced.set
    task = asyncio.create_task(discovery.advertise_daemon("192.0.2.1", 19001))
    await asyncio.wait_for(announced.wait(), 1)
    assert not task.done()
    assert info.port == 19001 and "19001" in info.name
    assert set(info.properties) == {"hostname"}
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    zc.async_close.assert_awaited_once()


@pytest.mark.parametrize("failure", ["error", "timeout", "cancel"])
async def test_partial_registration_is_closed(fake_discovery, monkeypatch, failure):
    _, zc, _, _ = fake_discovery
    entered = asyncio.Event()

    async def register(*args, **kwargs):
        entered.set()
        if failure == "error":
            raise OSError("multicast unavailable")
        await asyncio.Event().wait()

    zc.async_register_service.side_effect = register
    monkeypatch.setattr(discovery, "_REGISTRATION_TIMEOUT", 0.05)
    task = asyncio.create_task(discovery.advertise_daemon("192.0.2.1", 19001))
    await asyncio.wait_for(entered.wait(), 1)
    if failure == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await asyncio.wait_for(task, 1)
    zc.async_close.assert_awaited_once()


async def test_loopback_listener_is_not_advertised(fake_discovery):
    _, _, _, constructed = fake_discovery
    await discovery.advertise_daemon("127.0.0.1", 19001)
    assert constructed == []
