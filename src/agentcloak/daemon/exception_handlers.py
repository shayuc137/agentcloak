"""FastAPI exception handlers — single source of truth for error responses.

All errors converge here so daemon responses always carry the three-field
envelope `{ok: false, error, hint, action}`. Responsibilities are split:
localhost gating lives in ``middleware.py``, this module is errors only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import orjson
import structlog
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentcloak.core.errors import AgentBrowserError

if TYPE_CHECKING:
    from fastapi import FastAPI, Request

__all__ = ["register_exception_handlers"]

logger = structlog.get_logger()


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all exception handlers to the FastAPI app."""

    @app.exception_handler(AgentBrowserError)
    async def _agent_error_handler(  # type: ignore[reportUnusedFunction]
        request: Request, exc: AgentBrowserError
    ) -> JSONResponse:
        # ``status_code`` lives on the exception class so handlers don't have
        # to branch on type — DialogBlockedError emits 409, ProfileError
        # variants 400/404/409, everything else 400 by default.
        logger.warning("agent_error", error=exc.error, hint=exc.hint)
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(  # type: ignore[reportUnusedFunction]
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        # When raised from dependencies/route code with a structured dict
        # detail (our three-field envelope), pass it through verbatim.
        detail: Any = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            return JSONResponse(
                status_code=exc.status_code,
                content=cast("dict[str, Any]", detail),
            )
        hint = str(cast("Any", detail)) if detail else ""
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "ok": False,
                "error": "http_error",
                "hint": hint,
                "action": "check request and retry",
            },
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(  # type: ignore[reportUnusedFunction]
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Surface the first validation error as the hint — agents can scan the
        # full list under `errors` if they need more detail.
        # Validator contexts can contain exceptions and non-finite numeric inputs.
        errors: list[dict[str, Any]] = orjson.loads(
            orjson.dumps(exc.errors(), default=str)
        )
        first: dict[str, Any] = errors[0] if errors else {}
        loc_parts: list[Any] = list(first.get("loc", []))
        loc = ".".join(str(p) for p in loc_parts)
        msg = str(first.get("msg", "validation error"))
        return JSONResponse(
            status_code=422,
            content={
                "ok": False,
                "error": "invalid_request",
                "hint": f"{loc}: {msg}" if loc else msg,
                "action": "fix the request body and retry",
                "errors": errors,
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(  # type: ignore[reportUnusedFunction]
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("unhandled_error", path=request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": "internal_error",
                "hint": str(exc),
                "action": "retry the request, or run 'snapshot' to refresh page state",
            },
        )
