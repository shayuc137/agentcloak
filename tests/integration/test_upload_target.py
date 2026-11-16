"""Uploading via a visible button ref fails clearly instead of a raw 500."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from agentcloak.core.errors import BackendError

if TYPE_CHECKING:
    from pathlib import Path


async def test_upload_to_button_ref_explains_and_input_ref_works(
    browser_context: Any, local_server: str, tmp_path: Path
) -> None:
    sample = tmp_path / "po.pdf"
    sample.write_bytes(b"%PDF-1.4 probe")
    await browser_context.navigate(f"{local_server}/upload.html")
    snap = await browser_context.snapshot(mode="compact")
    button = next(i for i, r in snap.selector_map.items() if r.text == "上传附件")

    # DOS hit this as "Node is not an HTMLInputElement" -> unhandled 500.
    with pytest.raises(BackendError) as exc_info:
        await browser_context.upload(button, [str(sample)])
    assert exc_info.value.error == "upload_target_not_file_input"
    assert "上传附件" in exc_info.value.hint

    result = await browser_context.upload(None, [str(sample)])
    assert result["ok"] is True and result["files"] == ["po.pdf"]
    names = await browser_context.evaluate(
        "Array.from(document.querySelector('#file').files).map(f => f.name)"
    )
    assert names == ["po.pdf"]
