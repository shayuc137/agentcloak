"""Observation hiding must not replace a document before its HTML is parsed."""

from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.asyncio
async def test_hide_and_script_init_preserve_document(
    browser_context: Any, local_server: str
) -> None:
    ctx = browser_context
    await ctx.navigate(f"{local_server}/index.html")
    await ctx.hide_manager.apply()
    await ctx.screenshot()
    identifier = await ctx.script_manager.add("window.hideInitProbe = true")
    try:
        await ctx.navigate(f"{local_server}/form.html")
        result = await ctx.evaluate(
            "({root:document.documentElement.tagName,body:!!document.body,"
            "inputs:document.querySelectorAll('input').length,"
            "script:window.hideInitProbe,style:!!document.getElementById('__cloak_hide__')})"
        )
        assert result["root"] == "HTML"
        assert result["body"] is True
        assert result["inputs"] > 0
        assert result["script"] is True
        assert result["style"] is True
    finally:
        await ctx.script_manager.remove(identifier)
