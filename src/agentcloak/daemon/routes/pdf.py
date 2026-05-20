"""PDF-export route (7a R6) — render the current page to PDF.

``page.pdf()`` (CDP ``Page.printToPDF``) only works in headless Chromium; the
backend raises ``pdf_not_supported`` otherwise and that bubbles to the global
handler. Like screenshot, the route either writes the bytes to ``output_path``
(returning ``path`` + ``size``) or base64-encodes them for the CLI/MCP to
decode.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter

# ``screenshot_to_base64`` lives on the abstract base module so daemon code
# stays backend-agnostic (layer isolation: daemon → BrowserContextBase).
from agentcloak.browser.base import screenshot_to_base64
from agentcloak.daemon.dependencies import BrowserCtxDep  # noqa: TC001
from agentcloak.daemon.models import OkEnvelope, PdfRequest, PdfResponse
from agentcloak.daemon.routes._helpers import _ok

__all__ = ["router"]

router = APIRouter()


@router.post("/pdf", response_model=OkEnvelope[PdfResponse])
async def handle_pdf(body: PdfRequest, ctx: BrowserCtxDep) -> dict[str, Any]:
    # Map the request fields to the option vocabulary the backends accept
    # (CDP ``_pdf_options_to_cdp`` / Playwright ``page.pdf(**options)``).
    options: dict[str, Any] = {"format": body.format, "landscape": body.landscape}
    if body.scale is not None:
        options["scale"] = body.scale
    if body.margin is not None:
        options["margin"] = body.margin
    if body.page_ranges:
        options["pageRanges"] = body.page_ranges

    raw = await ctx.pdf(options=options)

    if body.output_path:
        dest = Path(body.output_path).expanduser()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        data = {"path": str(dest), "size": len(raw)}
        return _ok(data, seq=ctx.seq)

    data = {"base64": screenshot_to_base64(raw), "size": len(raw)}
    return _ok(data, seq=ctx.seq)
