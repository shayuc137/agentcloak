"""Parse cookies out of a Copy-as-cURL string.

Browsers' DevTools "Copy as cURL" puts the session cookies into either a
``-H 'Cookie: a=1; b=2'`` header or a ``-b/--cookie 'a=1; b=2'`` flag. Agents
routinely have such a string on hand (the user pastes it from their logged-in
browser), so ``cookies set --curl '<string>'`` lets them seed a session
without hand-authoring JSON. Mirrors agent-browser's ``cookies set --curl``.

The request URL in the command supplies the cookie ``domain`` (and the
``secure`` flag for https), so the resulting cookies can be fed straight into
``context.add_cookies`` / CDP ``Network.setCookie``.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

__all__ = ["parse_curl_cookies"]

# ``-H`` / ``--header`` with a single- or double-quoted value.
_HEADER_RE = re.compile(
    r"(?:-H|--header)\s+(['\"])(.*?)\1",
    re.IGNORECASE | re.DOTALL,
)
# ``-b`` / ``--cookie`` with a quoted value.
_COOKIE_FLAG_RE = re.compile(
    r"(?:-b|--cookie)\s+(['\"])(.*?)\1",
    re.IGNORECASE | re.DOTALL,
)
# First quoted or bare token that looks like an http(s) URL → the request URL.
_URL_RE = re.compile(r"(?:^|\s)(['\"]?)(https?://[^\s'\"]+)\1")


def _cookie_pairs(blob: str) -> list[tuple[str, str]]:
    """Split a ``a=1; b=2`` cookie string into (name, value) pairs."""
    pairs: list[tuple[str, str]] = []
    for chunk in blob.split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        name, _, value = chunk.partition("=")
        name = name.strip()
        if name:
            pairs.append((name, value.strip()))
    return pairs


def parse_curl_cookies(curl_str: str) -> list[dict[str, Any]]:
    """Extract cookie objects from a Copy-as-cURL command string.

    Returns a list of ``{name, value, domain, path, secure}`` dicts ready for
    ``context.add_cookies``. Cookie values from both the ``Cookie:`` header and
    a ``-b/--cookie`` flag are merged (later occurrences win on name clash).
    Returns an empty list when no cookies are present.
    """
    # Strip shell line-continuations so multi-line pastes parse as one command.
    text = curl_str.replace("\\\n", " ").replace("\\\r\n", " ")

    url_match = _URL_RE.search(text)
    domain = ""
    secure = False
    if url_match:
        parsed = urlparse(url_match.group(2))
        domain = parsed.hostname or ""
        secure = parsed.scheme.lower() == "https"

    merged: dict[str, str] = {}

    for _, header_value in _HEADER_RE.findall(text):
        name_part, sep, val_part = header_value.partition(":")
        if sep and name_part.strip().lower() == "cookie":
            for name, value in _cookie_pairs(val_part):
                merged[name] = value

    for _, flag_value in _COOKIE_FLAG_RE.findall(text):
        for name, value in _cookie_pairs(flag_value):
            merged[name] = value

    return [
        {
            "name": name,
            "value": value,
            "domain": domain,
            "path": "/",
            "secure": secure,
        }
        for name, value in merged.items()
    ]
