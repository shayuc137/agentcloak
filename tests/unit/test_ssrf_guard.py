"""SSRF guard unit tests.

Validates that daemon-side outbound HTTP requests are blocked when targeting
private, loopback, link-local, or reserved IP ranges. Also tests the httpx
request hook for redirect-hop validation.

GPT Pro review finding P0 (2026-06-27).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentcloak.core.errors import SecurityError
from agentcloak.core.ssrf_guard import (
    is_blocked_ip,
    ssrf_request_hook,
    validate_download_url,
    validate_outbound_url,
)


class TestIsBlockedIp:
    """Low-level IP blocklist checks."""

    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",
            "127.0.0.2",
            "10.0.0.1",
            "10.255.255.255",
            "172.16.0.1",
            "172.31.255.255",
            "192.168.1.1",
            "192.168.255.255",
            "169.254.169.254",
            "0.0.0.0",
            "100.64.0.1",
            "224.0.0.1",
            "240.0.0.1",
            "::1",
            "fc00::1",
            "fe80::1",
            "ff02::1",
        ],
    )
    def test_blocked_ips(self, ip: str) -> None:
        assert is_blocked_ip(ip) is True

    @pytest.mark.parametrize(
        "ip",
        [
            "8.8.8.8",
            "1.1.1.1",
            "198.18.0.1",
            "198.19.255.255",
            "93.184.216.34",
            "2607:f8b0:4004:800::200e",
        ],
    )
    def test_allowed_ips(self, ip: str) -> None:
        assert is_blocked_ip(ip) is False

    def test_invalid_ip_is_blocked(self) -> None:
        assert is_blocked_ip("not-an-ip") is True

    def test_ipv4_mapped_ipv6(self) -> None:
        assert is_blocked_ip("::ffff:127.0.0.1") is True
        assert is_blocked_ip("::ffff:8.8.8.8") is False


class TestValidateOutboundUrl:
    """Entry-point URL validation."""

    def test_public_https_allowed(self) -> None:
        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]
            validate_outbound_url("https://example.com/api")

    def test_loopback_blocked(self) -> None:
        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(2, 1, 6, "", ("127.0.0.1", 80))]
            with pytest.raises(SecurityError) as exc_info:
                validate_outbound_url("http://localhost:18765/health")
            assert exc_info.value.error == "outbound_target_blocked"

    def test_cloud_metadata_blocked(self) -> None:
        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(2, 1, 6, "", ("169.254.169.254", 80))]
            with pytest.raises(SecurityError) as exc_info:
                validate_outbound_url("http://169.254.169.254/latest/meta-data/")
            assert exc_info.value.error == "outbound_target_blocked"

    def test_private_ip_blocked(self) -> None:
        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(2, 1, 6, "", ("192.168.1.100", 80))]
            with pytest.raises(SecurityError) as exc_info:
                validate_outbound_url("http://internal-host.local/secret")
            assert exc_info.value.error == "outbound_target_blocked"

    def test_mixed_resolution_blocked(self) -> None:
        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [
                (2, 1, 6, "", ("93.184.216.34", 80)),
                (2, 1, 6, "", ("10.0.0.1", 80)),
            ]
            with pytest.raises(SecurityError):
                validate_outbound_url("http://dns-rebind.example.com/")

    def test_bad_scheme_blocked(self) -> None:
        with pytest.raises(SecurityError) as exc_info:
            validate_outbound_url("ftp://example.com/file")
        assert exc_info.value.error == "outbound_scheme_blocked"

    def test_no_host_blocked(self) -> None:
        with pytest.raises(SecurityError) as exc_info:
            validate_outbound_url("http://")
        assert exc_info.value.error == "outbound_url_invalid"

    def test_dns_failure_blocked(self) -> None:
        import socket

        with patch("socket.getaddrinfo", side_effect=socket.gaierror("nope")):
            with pytest.raises(SecurityError) as exc_info:
                validate_outbound_url("http://nonexistent.invalid/")
            assert exc_info.value.error == "outbound_dns_failed"


class TestValidateDownloadUrlBackCompat:
    """Back-compat alias uses download_* error codes."""

    def test_uses_download_error_codes(self) -> None:
        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(2, 1, 6, "", ("127.0.0.1", 80))]
            with pytest.raises(SecurityError) as exc_info:
                validate_download_url("http://localhost/")
            assert exc_info.value.error == "download_target_blocked"


class TestSsrfRequestHook:
    """httpx event hook for redirect-hop validation."""

    def test_blocks_private_redirect_target(self) -> None:
        request = MagicMock()
        request.url = "http://127.0.0.1:18765/health"
        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(2, 1, 6, "", ("127.0.0.1", 18765))]
            with pytest.raises(SecurityError) as exc_info:
                ssrf_request_hook(request)
            assert exc_info.value.error == "outbound_target_blocked"

    def test_allows_public_target(self) -> None:
        request = MagicMock()
        request.url = "https://api.example.com/data"
        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]
            ssrf_request_hook(request)


class TestFetchIntegration:
    """Verify base.fetch() calls SSRF guard."""

    @pytest.mark.asyncio
    async def test_fetch_blocks_private_url(self) -> None:

        from agentcloak.browser.remote_ctx import RemoteBridgeContext

        ws = MagicMock()
        ws.closed = False
        ctx = RemoteBridgeContext(bridge_ws=ws)
        ctx._check_debugger_paused = MagicMock()
        ctx._check_browser_alive = MagicMock()

        with patch("socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(2, 1, 6, "", ("127.0.0.1", 80))]
            with pytest.raises(SecurityError) as exc_info:
                await ctx.fetch("http://localhost/secret")
            assert exc_info.value.error == "outbound_target_blocked"
