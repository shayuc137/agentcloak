"""mDNS service discovery for daemon ↔ bridge auto-connect.

``zeroconf`` is an optional dependency with no type stubs of its own. The
file-level pyright suppressions cover the resulting "unknown" diagnostics so
we don't have to spray ``# pyright: ignore`` over every line that touches a
zeroconf object.
"""

# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
# pyright: reportUnknownParameterType=false, reportUnknownArgumentType=false
# pyright: reportUnusedImport=false

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Any

import structlog

__all__ = ["advertise_daemon", "discover_daemon"]

logger = structlog.get_logger()

SERVICE_TYPE = "_agentcloak._tcp.local."
_REGISTRATION_TIMEOUT = 5.0
_CLOSE_TIMEOUT = 5.0


def _has_zeroconf() -> bool:
    try:
        import zeroconf  # pyright: ignore[reportMissingImports]  # noqa: F401

        return True
    except ImportError:
        return False


async def advertise_daemon(host: str, port: int) -> None:
    """Advertise a ready listener until cancelled; discovery never owns readiness."""
    if not _has_zeroconf():
        logger.debug("zeroconf_not_available")
        return
    zc: Any = None
    try:
        from zeroconf import ServiceInfo  # pyright: ignore[reportMissingImports]
        from zeroconf.asyncio import (  # pyright: ignore[reportMissingImports]
            AsyncZeroconf,
        )

        addresses = await asyncio.get_running_loop().getaddrinfo(
            host or "0.0.0.0", port, type=socket.SOCK_STREAM
        )
        address = ipaddress.ip_address(addresses[0][4][0])
        if address.is_unspecified:
            address = ipaddress.ip_address(
                _get_local_ip(
                    socket.AF_INET6 if address.version == 6 else socket.AF_INET
                )
            )
        if address.is_loopback:
            logger.debug("mdns_not_advertised", reason="loopback_only_listener")
            return
        hostname = socket.gethostname()
        name = f"agentcloak-{hostname[:32]}-{port}.{SERVICE_TYPE}"
        info = ServiceInfo(
            SERVICE_TYPE,
            name,
            addresses=[address.packed],
            port=port,
            properties={"hostname": hostname},
        )
        zc = AsyncZeroconf()
        # The second await covers the announcement task returned by zeroconf.
        async with asyncio.timeout(_REGISTRATION_TIMEOUT):
            await (await zc.async_register_service(info, allow_name_change=True))
        logger.info("mdns_registered", service=info.name, ip=str(address), port=port)
        await asyncio.Event().wait()
    except Exception as exc:
        logger.warning("mdns_register_failed", error=str(exc))
    finally:
        if zc is not None:
            try:
                async with asyncio.timeout(_CLOSE_TIMEOUT):
                    await zc.async_close()
                logger.info("mdns_unregistered")
            except Exception as exc:
                logger.warning("mdns_close_failed", error=str(exc))


def discover_daemon(timeout: float = 3.0) -> str | None:
    if not _has_zeroconf():
        return None

    try:
        import time

        from zeroconf import (  # pyright: ignore[reportMissingImports]
            ServiceBrowser,
            ServiceListener,
            Zeroconf,
        )

        zc = Zeroconf()
        found: list[dict[str, Any]] = []

        # zeroconf >= 0.150 requires listeners to subclass ServiceListener;
        # duck-typed classes are rejected at ServiceBrowser construction.
        class Listener(ServiceListener):  # type: ignore[misc]
            def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
                info = zc.get_service_info(type_, name)
                if info and info.port:
                    addresses = info.parsed_addresses()
                    ip = addresses[0] if addresses else "127.0.0.1"
                    props = {
                        k.decode(): v.decode()
                        for k, v in (info.properties or {}).items()
                        if v is not None
                    }
                    found.append(
                        {
                            "ip": ip,
                            "port": info.port,
                            "token": props.get("token"),
                        }
                    )

            def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
                pass

            def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
                pass

        ServiceBrowser(zc, SERVICE_TYPE, Listener())
        time.sleep(timeout)
        zc.close()

        if found:
            d = found[0]
            host = f"[{d['ip']}]" if ":" in d["ip"] else d["ip"]
            url = f"ws://{host}:{d['port']}/ext"
            logger.info("mdns_discovered", url=url)
            return url

        return None
    except Exception as exc:
        logger.debug("mdns_discover_failed", error=str(exc))
        return None


def _get_local_ip(family: int = socket.AF_INET) -> str:
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            target = "2001:4860:4860::8888" if family == socket.AF_INET6 else "8.8.8.8"
            sock.connect((target, 80))
            return str(sock.getsockname()[0])
    except Exception:
        return "::1" if family == socket.AF_INET6 else "127.0.0.1"
