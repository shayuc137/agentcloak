"""SSRF guard for daemon-side outbound HTTP requests.

Any daemon-process HTTP request to an attacker-controlled URL is an SSRF
vector: prompt-injected agents could fetch ``http://169.254.169.254/...``
(cloud metadata), ``http://127.0.0.1:port/...`` (daemon-local services),
or any RFC1918 host. We resolve the hostname and reject if *any* resolved
address is private, loopback, link-local, or otherwise non-public.

Two layers of defence:

1. **Entry-point validation** — ``validate_outbound_url(url)`` checks the
   initial URL before any request is made. Called by ``base.fetch()`` and
   ``base.download_url()``.

2. **Per-hop redirect validation** — ``ssrf_request_hook(request)`` is an
   httpx event hook that fires before *every* request including redirect
   hops, preventing a public URL from 302-ing into a private network.

Modelled on pinchtab's ``downloadURLGuard``.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

from agentcloak.core.errors import SecurityError

__all__ = [
    "is_blocked_ip",
    "ssrf_request_hook",
    "validate_download_url",
    "validate_outbound_url",
]

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
    """Return True if the literal IP must not be reached.

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


def _validate_url(url: str, *, label: str = "outbound") -> None:
    """Core validation: scheme check + DNS resolve + IP blocklist."""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise SecurityError(
            error=f"{label}_scheme_blocked",
            hint=f"URL scheme '{scheme or '(none)'}' is not allowed",
            action="use an http:// or https:// URL",
        )

    host = parsed.hostname
    if not host:
        raise SecurityError(
            error=f"{label}_url_invalid",
            hint="URL has no host",
            action="provide a fully-qualified http(s) URL",
        )

    try:
        infos = socket.getaddrinfo(host, parsed.port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SecurityError(
            error=f"{label}_dns_failed",
            hint=f"Could not resolve host '{host}': {exc}",
            action="check the URL hostname",
        ) from exc

    resolved = {info[4][0] for info in infos}
    if not resolved:
        raise SecurityError(
            error=f"{label}_dns_failed",
            hint=f"Host '{host}' did not resolve to any address",
            action="check the URL hostname",
        )

    for ip in resolved:
        if is_blocked_ip(str(ip)):
            raise SecurityError(
                error=f"{label}_target_blocked",
                hint=f"Host '{host}' resolves to a non-public address ({ip})",
                action=("requests to private/loopback/link-local hosts are blocked"),
            )


def validate_outbound_url(url: str) -> None:
    """Raise :class:`SecurityError` if ``url`` resolves to a non-public host.

    Validates the initial URL before any request. For redirect protection,
    also use :func:`ssrf_request_hook` as an httpx event hook.
    """
    _validate_url(url, label="outbound")


def validate_download_url(url: str) -> None:
    """Back-compat alias — same validation, ``download_*`` error codes."""
    _validate_url(url, label="download")


def ssrf_request_hook(request: Any) -> None:
    """httpx event hook that validates each request URL including redirects.

    Usage::

        async with httpx.AsyncClient(
            event_hooks={"request": [ssrf_request_hook]},
        ) as client:
            ...

    Fires before every outbound request. On a redirect chain
    ``A → 302 → B → 302 → C``, this validates A, B, and C individually,
    blocking the chain the moment any hop targets a non-public host.
    """
    url_str = str(request.url)
    _validate_url(url_str, label="outbound")
