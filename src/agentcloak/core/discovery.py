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

import socket
from typing import Any

import structlog

__all__ = ["discover_daemon", "register_daemon", "unregister_daemon"]

logger = structlog.get_logger()

SERVICE_TYPE = "_agentcloak._tcp.local."
SERVICE_NAME = "agentcloak-daemon._agentcloak._tcp.local."

_registration: Any = None


def _has_zeroconf() -> bool:
    try:
        import zeroconf  # pyright: ignore[reportMissingImports]  # noqa: F401

        return True
    except ImportError:
        return False


def register_daemon(port: int, token: str | None = None) -> bool:
    global _registration
    if not _has_zeroconf():
        logger.debug("zeroconf_not_available")
        return False

    try:
        from zeroconf import ServiceInfo, Zeroconf  # pyright: ignore[reportMissingImports]  # noqa: I001

        hostname = socket.gethostname()
        local_ip = _get_local_ip()

        properties: dict[str, str] = {"hostname": hostname}
        # Token is NOT broadcast over mDNS — must be obtained via session file

        info = ServiceInfo(
            SERVICE_TYPE,
            SERVICE_NAME,
            addresses=[socket.inet_aton(local_ip)],
            port=port,
            properties=properties,
        )

        zc = Zeroconf()
        zc.register_service(info)
        _registration = (zc, info)

        logger.info(
            "mdns_registered",
            service=SERVICE_NAME,
            ip=local_ip,
            port=port,
        )
        return True
    except Exception as exc:
        logger.debug("mdns_register_failed", error=str(exc))
        return False


def unregister_daemon() -> None:
    global _registration
    if _registration is None:
        return
    try:
        zc, info = _registration
        zc.unregister_service(info)
        zc.close()
        _registration = None
        logger.info("mdns_unregistered")
    except Exception:
        _registration = None


def discover_daemon(timeout: float = 3.0) -> str | None:
    if not _has_zeroconf():
        return None

    try:
        import time  # noqa: I001

        from zeroconf import ServiceBrowser, Zeroconf  # pyright: ignore[reportMissingImports]

        zc = Zeroconf()
        found: list[dict[str, Any]] = []

        class Listener:
            def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
                info = zc.get_service_info(type_, name)
                if info and info.port:
                    addresses = info.parsed_addresses()
                    ip = addresses[0] if addresses else "127.0.0.1"
                    props = {
                        k.decode(): v.decode()
                        for k, v in (info.properties or {}).items()
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
            url = f"ws://{d['ip']}:{d['port']}/ext"
            logger.info("mdns_discovered", url=url)
            return url

        return None
    except Exception as exc:
        logger.debug("mdns_discover_failed", error=str(exc))
        return None


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return str(ip)
    except Exception:
        return "127.0.0.1"
