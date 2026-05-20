"""SSRF guard for direct-download URLs.

The ``download url`` capability fetches an arbitrary URL server-side using the
browser's cookies. Without a guard, an agent that has been prompt-injected by
a page could be steered into fetching ``http://169.254.169.254/...`` (cloud
metadata), ``http://127.0.0.1:port/...`` (daemon-local services), or any
RFC1918 host on the daemon's network — a classic SSRF. We resolve the
hostname and reject the request if *any* resolved address is private,
loopback, link-local, or otherwise non-public.

Modelled on pinchtab's ``downloadURLGuard``. Validation happens in ``core`` so
both backends and the daemon route can share it, and it is part of the IDPI
security story (PRD R2).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from agentcloak.core.errors import SecurityError

__all__ = ["is_blocked_ip", "validate_download_url"]

_ALLOWED_SCHEMES = frozenset({"http", "https"})


_BLOCKED_NETWORKS_V4 = [
    ipaddress.IPv4Network("0.0.0.0/8"),
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("100.64.0.0/10"),
    ipaddress.IPv4Network("127.0.0.0/8"),
    ipaddress.IPv4Network("169.254.0.0/16"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("224.0.0.0/4"),
    ipaddress.IPv4Network("240.0.0.0/4"),
]

_BLOCKED_NETWORKS_V6 = [
    ipaddress.IPv6Network("::1/128"),
    ipaddress.IPv6Network("::/128"),
    ipaddress.IPv6Network("fc00::/7"),
    ipaddress.IPv6Network("fe80::/10"),
    ipaddress.IPv6Network("ff00::/8"),
]


def is_blocked_ip(ip: str) -> bool:
    """Return True if the literal IP must not be reached for a download.

    Uses an explicit blocklist instead of Python's ``is_private`` (which
    includes 198.18.0.0/15 — commonly used by fake-IP DNS proxies like
    clash/surge/passwall and would false-positive for many users).

    Blocked ranges:
    - 0.0.0.0/8 (unspecified), 127.0.0.0/8 (loopback)
    - 10/8, 172.16/12, 192.168/16 (RFC 1918 private)
    - 100.64/10 (CGNAT), 169.254/16 (link-local, cloud metadata)
    - 224/4 (multicast), 240/4 (reserved)
    - IPv6: ::1 (loopback), fc00::/7 (ULA), fe80::/10 (link-local), ff00::/8 (multicast)

    Intentionally NOT blocked: 198.18.0.0/15 (benchmarking / fake-IP proxies).
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True

    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped

    if isinstance(addr, ipaddress.IPv4Address):
        return any(addr in net for net in _BLOCKED_NETWORKS_V4)
    return any(addr in net for net in _BLOCKED_NETWORKS_V6)


def validate_download_url(url: str) -> None:
    """Raise :class:`SecurityError` if ``url`` resolves to a non-public host.

    Performs scheme validation then DNS resolution, checking every resolved
    address. A hostname that resolves to a mix of public and private
    addresses is rejected (DNS-rebinding defence — we can't pin the address
    the actual download will use here, so any private answer is fatal).
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise SecurityError(
            error="download_scheme_blocked",
            hint=f"Download URL scheme '{scheme or '(none)'}' is not allowed",
            action="use an http:// or https:// URL",
        )

    host = parsed.hostname
    if not host:
        raise SecurityError(
            error="download_url_invalid",
            hint="Download URL has no host",
            action="provide a fully-qualified http(s) URL",
        )

    # A bare IP literal is checked directly; a hostname is resolved first.
    try:
        infos = socket.getaddrinfo(host, parsed.port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SecurityError(
            error="download_dns_failed",
            hint=f"Could not resolve host '{host}': {exc}",
            action="check the URL hostname",
        ) from exc

    resolved = {info[4][0] for info in infos}
    if not resolved:
        raise SecurityError(
            error="download_dns_failed",
            hint=f"Host '{host}' did not resolve to any address",
            action="check the URL hostname",
        )

    for ip in resolved:
        if is_blocked_ip(str(ip)):
            raise SecurityError(
                error="download_target_blocked",
                hint=f"Host '{host}' resolves to a non-public address ({ip})",
                action="downloads to private/loopback/link-local hosts are blocked",
            )
