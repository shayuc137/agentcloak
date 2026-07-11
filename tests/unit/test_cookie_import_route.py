"""Cookie import route normalization tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from agentcloak.core.text_renderers import render_cookies_import_text
from agentcloak.daemon.models import CookiesImportRequest
from agentcloak.daemon.routes.interaction import handle_cookies_import


@pytest.mark.asyncio
async def test_cookie_import_normalizes_and_reports_skipped_entries() -> None:
    browser_context = MagicMock()
    browser_context.add_cookies = AsyncMock()
    ctx = MagicMock()
    ctx._get_browser_context.return_value = browser_context
    type(ctx).seq = PropertyMock(return_value=9)
    response = await handle_cookies_import(
        CookiesImportRequest(
            cookies=[
                {
                    "name": "sid",
                    "value": "abc",
                    "domain": ".example.com",
                    "path": "/",
                    "expirationDate": 1795752619.5,
                    "sameSite": "no_restriction",
                    "hostOnly": False,
                },
                {"name": "missing-value"},
            ]
        ),
        ctx,
    )

    assert response == {
        "ok": True,
        "seq": 9,
        "data": {"imported": 1, "skipped": 1},
    }
    browser_context.add_cookies.assert_awaited_once_with(
        [
            {
                "name": "sid",
                "value": "abc",
                "domain": ".example.com",
                "path": "/",
                "expires": 1795752619.5,
                "sameSite": "None",
            }
        ]
    )


def test_cookie_import_renderer_reports_skipped_entries() -> None:
    assert (
        render_cookies_import_text({"imported": 2, "skipped": 1})
        == "imported 2 cookies; skipped 1 invalid cookies"
    )
