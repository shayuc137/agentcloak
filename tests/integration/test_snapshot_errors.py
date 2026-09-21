"""Browser runtime coverage for accessible controls and JavaScript failures."""

from __future__ import annotations

from typing import Any

import pytest

from agentcloak.core.errors import BackendError


async def test_nested_controls_are_addressable(
    browser_context: Any, local_server: str
) -> None:
    await browser_context.navigate(f"{local_server}/controls.html")
    snap = await browser_context.snapshot(mode="compact")
    colleague = next(
        i
        for i, ref in snap.selector_map.items()
        if ref.text.startswith("当前同事 Alex")
    )
    await browser_context.action("click", str(colleague))
    snap = await browser_context.snapshot(mode="compact", selector="#panel")
    assert "Outside" not in snap.tree_text
    for label, expected in [
        ("设置", "settings"),
        ("自定义面板", "panel"),
        ("程序聚焦", "negative"),
    ]:
        ref_id = next(i for i, ref in snap.selector_map.items() if ref.text == label)
        await browser_context.action("click", str(ref_id))
        assert await browser_context.evaluate("window.selected") == expected


@pytest.mark.parametrize("world", ["main", "isolated"])
async def test_javascript_throws_fail_but_error_strings_succeed(
    browser_context: Any, world: str
) -> None:
    for expression in [
        "throw new Error('error-probe')",
        "Promise.reject(new Error('error-probe'))",
    ]:
        with pytest.raises(BackendError, match="error-probe"):
            await browser_context.evaluate(expression, world=world)
    assert (
        await browser_context.evaluate("'Error: ordinary data'", world=world)
        == "Error: ordinary data"
    )
