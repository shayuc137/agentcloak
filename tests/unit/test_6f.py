"""Unit tests for the Phase 6f DX backlog batch.

Covers three features end-to-end at the route / backend / surface boundaries:

* R1 — upload auto-find (hidden file inputs via querySelectorAll)
* R2 — download wait-click (arm waiter → click → await, atomic)
* R3 — evaluate presets (canned reverse-engineering snippets)

The browser context is mocked (MagicMock + AsyncMock) so routes run without a
real browser; backend ``_impl`` behaviour is exercised against the real
PlaywrightContext with a mocked Playwright page.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest
from fastapi.testclient import TestClient

from agentcloak.daemon.app import create_app


def _ctx() -> MagicMock:
    ctx = MagicMock()
    type(ctx).seq = PropertyMock(return_value=7)
    return ctx


def _client(ctx: MagicMock) -> TestClient:
    app = create_app()
    app.state.browser_ctx = ctx
    return TestClient(app)


# ---------------------------------------------------------------------------
# R3: evaluate presets
# ---------------------------------------------------------------------------


class TestEvaluatePresetsModule:
    def test_all_presets_present(self) -> None:
        from agentcloak.core.evaluate_presets import EVALUATE_PRESETS

        assert set(EVALUATE_PRESETS) == {
            "vue_inspect",
            "react_inspect",
            "jwt_decode",
            "cookie_parse",
            "storage_dump",
        }

    @pytest.mark.parametrize(
        "name",
        ["vue_inspect", "react_inspect", "jwt_decode", "cookie_parse", "storage_dump"],
    )
    def test_preset_js_is_balanced_iife(self, name: str) -> None:
        """Each preset must be a self-invoking function returning a value.

        We can't run a JS engine in unit tests, but we can guard the structural
        invariants that matter: it's an IIFE, it stringifies its result, and the
        braces/parens are balanced (catches truncated edits).
        """
        from agentcloak.core.evaluate_presets import EVALUATE_PRESETS

        js = EVALUATE_PRESETS[name].strip()
        assert js.startswith("(()") or js.startswith("(async")
        assert js.endswith(")()")
        assert "JSON.stringify" in js
        assert "try" in js and "catch" in js
        assert js.count("{") == js.count("}")
        assert js.count("(") == js.count(")")

    def test_get_preset_js_returns_template(self) -> None:
        from agentcloak.core.evaluate_presets import EVALUATE_PRESETS, get_preset_js

        assert get_preset_js("jwt_decode") == EVALUATE_PRESETS["jwt_decode"]

    def test_get_preset_js_unknown_raises_with_list(self) -> None:
        from agentcloak.core.errors import BackendError
        from agentcloak.core.evaluate_presets import get_preset_js

        with pytest.raises(BackendError) as exc:
            get_preset_js("nope")
        assert exc.value.error == "unknown_preset"
        # The action lists every valid preset so the agent can recover.
        assert "vue_inspect" in exc.value.action
        assert "storage_dump" in exc.value.action


class TestEvaluatePresetRoute:
    def test_preset_runs_in_main_world(self) -> None:
        from agentcloak.core.evaluate_presets import EVALUATE_PRESETS

        ctx = _ctx()
        ctx.evaluate = AsyncMock(return_value='{"detected": false}')
        resp = _client(ctx).post("/evaluate", json={"preset": "vue_inspect"})
        assert resp.status_code == 200
        # The route substitutes the preset JS and forces world="main".
        args, kwargs = ctx.evaluate.await_args
        assert args[0] == EVALUATE_PRESETS["vue_inspect"]
        assert kwargs["world"] == "main"

    def test_preset_forces_main_even_when_isolated_requested(self) -> None:
        ctx = _ctx()
        ctx.evaluate = AsyncMock(return_value="{}")
        resp = _client(ctx).post(
            "/evaluate", json={"preset": "storage_dump", "world": "isolated"}
        )
        assert resp.status_code == 200
        assert ctx.evaluate.await_args.kwargs["world"] == "main"

    def test_unknown_preset_is_400(self) -> None:
        ctx = _ctx()
        ctx.evaluate = AsyncMock()
        resp = _client(ctx).post("/evaluate", json={"preset": "bogus"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "unknown_preset"
        ctx.evaluate.assert_not_awaited()

    def test_js_still_works_without_preset(self) -> None:
        ctx = _ctx()
        ctx.evaluate = AsyncMock(return_value=42)
        resp = _client(ctx).post(
            "/evaluate", json={"js": "1 + 41", "world": "isolated"}
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["result"] == 42
        args, kwargs = ctx.evaluate.await_args
        assert args[0] == "1 + 41"
        assert kwargs["world"] == "isolated"


# ---------------------------------------------------------------------------
# R1: upload auto-find
# ---------------------------------------------------------------------------


class TestUploadAutoFindRoute:
    def test_explicit_index_passthrough(self) -> None:
        ctx = _ctx()
        ctx.upload = AsyncMock(
            return_value={"uploaded": 1, "index": 3, "files": ["a.png"], "seq": 8}
        )
        resp = _client(ctx).post("/upload", json={"index": 3, "files": ["/tmp/a.png"]})
        assert resp.status_code == 200
        ctx.upload.assert_awaited_once_with(3, ["/tmp/a.png"], nth=0)

    def test_auto_find_when_index_omitted(self) -> None:
        ctx = _ctx()
        ctx.upload = AsyncMock(
            return_value={
                "uploaded": 1,
                "files": ["a.png"],
                "candidates_count": 2,
                "used_nth": 1,
                "seq": 8,
            }
        )
        resp = _client(ctx).post("/upload", json={"files": ["/tmp/a.png"], "nth": 1})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["candidates_count"] == 2
        assert data["used_nth"] == 1
        # index=None triggers auto-find; nth is forwarded.
        ctx.upload.assert_awaited_once_with(None, ["/tmp/a.png"], nth=1)

    def test_no_file_input_found_bubbles(self) -> None:
        from agentcloak.core.errors import ElementNotFoundError

        ctx = _ctx()
        ctx.upload = AsyncMock(
            side_effect=ElementNotFoundError(
                error="no_file_input_found",
                hint="No <input type=file> elements on the page",
                action="check the page or pass --index",
            )
        )
        resp = _client(ctx).post("/upload", json={"files": ["/tmp/a.png"]})
        assert resp.status_code == 400
        assert resp.json()["error"] == "no_file_input_found"

    def test_missing_files_is_400(self) -> None:
        ctx = _ctx()
        ctx.upload = AsyncMock()
        resp = _client(ctx).post("/upload", json={"index": 1, "files": []})
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_files"
        ctx.upload.assert_not_awaited()


class TestUploadAutoFindBackend:
    """PlaywrightContext.upload auto-find against a mocked page."""

    def _ctx_with_inputs(self, count: int) -> Any:
        from agentcloak.browser.playwright_ctx import PlaywrightContext
        from agentcloak.core.seq import RingBuffer, SeqCounter

        handles = [MagicMock(name=f"input{i}") for i in range(count)]
        for h in handles:
            h.set_input_files = AsyncMock()

        page = MagicMock()
        page.on = MagicMock()
        page.url = "https://example.com"
        page.title = AsyncMock(return_value="Example")
        page.query_selector_all = AsyncMock(return_value=handles)

        ctx = PlaywrightContext(
            page=page,
            browser=MagicMock(),
            playwright=MagicMock(),
            seq_counter=SeqCounter(),
            ring_buffer=RingBuffer(),
        )
        return ctx, handles

    @pytest.mark.asyncio
    async def test_auto_find_picks_first_by_default(self, tmp_path: Any) -> None:
        f = tmp_path / "a.png"
        f.write_bytes(b"x")
        ctx, handles = self._ctx_with_inputs(3)
        result = await ctx.upload(None, [str(f)])
        assert result["candidates_count"] == 3
        assert result["used_nth"] == 0
        handles[0].set_input_files.assert_awaited_once()
        handles[1].set_input_files.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_auto_find_nth_selects_target(self, tmp_path: Any) -> None:
        f = tmp_path / "a.png"
        f.write_bytes(b"x")
        ctx, handles = self._ctx_with_inputs(3)
        result = await ctx.upload(None, [str(f)], nth=2)
        assert result["used_nth"] == 2
        handles[2].set_input_files.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_auto_find_no_inputs_raises(self, tmp_path: Any) -> None:
        from agentcloak.core.errors import ElementNotFoundError

        f = tmp_path / "a.png"
        f.write_bytes(b"x")
        ctx, _ = self._ctx_with_inputs(0)
        with pytest.raises(ElementNotFoundError) as exc:
            await ctx.upload(None, [str(f)])
        assert exc.value.error == "no_file_input_found"

    @pytest.mark.asyncio
    async def test_auto_find_nth_out_of_range_raises(self, tmp_path: Any) -> None:
        from agentcloak.core.errors import ElementNotFoundError

        f = tmp_path / "a.png"
        f.write_bytes(b"x")
        ctx, _ = self._ctx_with_inputs(2)
        with pytest.raises(ElementNotFoundError) as exc:
            await ctx.upload(None, [str(f)], nth=5)
        assert exc.value.error == "file_input_index_out_of_range"


# ---------------------------------------------------------------------------
# R2: download wait-click
# ---------------------------------------------------------------------------


class TestDownloadWaitClickRoute:
    def test_route_registered(self) -> None:
        app = create_app()
        spec = app.openapi()
        assert "/download/wait-click" in spec["paths"]
        assert "post" in spec["paths"]["/download/wait-click"]

    def test_wait_click_forwards_args(self) -> None:
        ctx = _ctx()
        ctx.download_wait_click = AsyncMock(
            return_value={
                "filename": "f.zip",
                "path": "/tmp/f.zip",
                "size": 5,
                "url": "https://x/f.zip",
                "source": "event",
            }
        )
        resp = _client(ctx).post(
            "/download/wait-click", json={"index": 5, "force": True}
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["path"] == "/tmp/f.zip"
        _, kwargs = ctx.download_wait_click.await_args
        assert kwargs["index"] == 5
        assert kwargs["force"] is True
        assert kwargs["output_dir"]  # defaults to temp dir

    def test_click_failure_bubbles(self) -> None:
        from agentcloak.core.errors import ElementNotFoundError

        ctx = _ctx()
        ctx.download_wait_click = AsyncMock(
            side_effect=ElementNotFoundError(
                error="element_not_found",
                hint="Index [9] not in selector_map",
                action="run snapshot first",
            )
        )
        resp = _client(ctx).post("/download/wait-click", json={"index": 9})
        assert resp.status_code == 400
        assert resp.json()["error"] == "element_not_found"


class TestDownloadWaitClickBackend:
    """BrowserContextBase.download_wait_click orchestration."""

    def _ctx(self) -> Any:
        from agentcloak.browser.playwright_ctx import PlaywrightContext
        from agentcloak.core.seq import RingBuffer, SeqCounter

        page = MagicMock()
        page.on = MagicMock()
        page.url = "https://example.com"
        page.title = AsyncMock(return_value="Example")
        ctx = PlaywrightContext(
            page=page,
            browser=MagicMock(),
            playwright=MagicMock(),
            seq_counter=SeqCounter(),
            ring_buffer=RingBuffer(),
        )
        return ctx

    @pytest.mark.asyncio
    async def test_click_then_download(self, tmp_path: Any) -> None:
        from agentcloak.browser.state import DownloadEntry

        ctx = self._ctx()
        clicked: dict[str, Any] = {}

        async def fake_action(kind: str, target: str, **kw: Any) -> dict[str, Any]:
            clicked["kind"] = kind
            clicked["target"] = target
            clicked["force"] = kw.get("force")
            return {"ok": True}

        ctx.action = AsyncMock(side_effect=fake_action)
        entry = DownloadEntry(
            filename="f.zip",
            path=str(tmp_path / "f.zip"),
            size=5,
            url="https://x/f.zip",
            source="event",
        )
        ctx._download_wait_impl = AsyncMock(return_value=entry)  # type: ignore[method-assign]

        result = await ctx.download_wait_click(
            index=5, output_dir=str(tmp_path), timeout=1.0, force=True
        )
        assert result["path"] == str(tmp_path / "f.zip")
        assert clicked == {"kind": "click", "target": "5", "force": True}
        ctx._download_wait_impl.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_click_failure_cancels_waiter(self, tmp_path: Any) -> None:
        from agentcloak.core.errors import ElementNotFoundError

        ctx = self._ctx()

        # The real _download_wait_impl runs on_armed (the click) after parking
        # the future; a failing click must propagate and not hang on timeout.
        ctx.action = AsyncMock(
            side_effect=ElementNotFoundError(
                error="element_not_found",
                hint="bad ref",
                action="snapshot",
            )
        )
        with pytest.raises(ElementNotFoundError):
            await ctx.download_wait_click(
                index=9, output_dir=str(tmp_path), timeout=1.0
            )
        assert ctx._download_waiters == []
