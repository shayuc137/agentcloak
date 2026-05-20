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


def is_blocked_ip(ip: str) -> bool:
    """Return True if the literal IP must not be reached for a download.

    Blocks loopback, private (RFC1918 / ULA), link-local, multicast,
    reserved, and unspecified ranges. IPv4-mapped IPv6 addresses are
    unwrapped first so ``::ffff:127.0.0.1`` can't sneak past the IPv4 checks.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        # Not a parseable IP — treat as blocked, the caller only feeds us
        # getaddrinfo output so an unparseable value is anomalous.
        return True

    # Unwrap IPv4-mapped IPv6 (``::ffff:a.b.c.d``) to apply IPv4 ranges.
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped

    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


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
