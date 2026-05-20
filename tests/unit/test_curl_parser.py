"""Unit tests for the Copy-as-cURL cookie parser (7a R3)."""

from __future__ import annotations

from agentcloak.core.curl_parser import parse_curl_cookies


class TestParseCurlCookies:
    def test_cookie_header(self) -> None:
        curl = "curl 'https://example.com/api' -H 'Cookie: sid=abc; theme=dark'"
        cookies = parse_curl_cookies(curl)
        by_name = {c["name"]: c for c in cookies}
        assert by_name["sid"]["value"] == "abc"
        assert by_name["theme"]["value"] == "dark"
        # Domain + secure derive from the request URL.
        assert by_name["sid"]["domain"] == "example.com"
        assert by_name["sid"]["secure"] is True

    def test_cookie_flag_short(self) -> None:
        curl = "curl http://example.com -b 'a=1; b=2'"
        cookies = parse_curl_cookies(curl)
        names = {c["name"] for c in cookies}
        assert names == {"a", "b"}
        # http:// → not secure.
        assert all(c["secure"] is False for c in cookies)

    def test_header_and_flag_merge_flag_wins(self) -> None:
        curl = (
            "curl 'https://x.com' -H 'Cookie: token=fromheader' "
            "--cookie 'token=fromflag'"
        )
        cookies = parse_curl_cookies(curl)
        assert len(cookies) == 1
        assert cookies[0]["value"] == "fromflag"

    def test_no_cookies_returns_empty(self) -> None:
        assert (
            parse_curl_cookies("curl https://x.com -H 'Accept: application/json'") == []
        )

    def test_multiline_continuation(self) -> None:
        curl = "curl 'https://x.com' \\\n  -H 'Cookie: a=1'"
        cookies = parse_curl_cookies(curl)
        assert cookies[0]["name"] == "a"
