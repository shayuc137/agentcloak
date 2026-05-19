"""ActionService — orchestrates action / batch / stale ref retry.

The route handler in ``daemon/routes.py`` used to inline ~150 lines of
stale-ref retry plus ``$N.path`` reference resolution. That logic moves here
so it can be tested in isolation and reused (e.g. by spells that drive the
context directly).
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import structlog

from agentcloak.core.errors import DialogBlockedError, ElementNotFoundError

__all__ = ["ActionService"]

logger = structlog.get_logger()

# ``$N.dotted.path`` reference syntax used by batch actions to pull values out
# of prior step results.
_REF_PATTERN = re.compile(r"^\$(\d+)\.(.+)$")


class ActionService:
    """Stateless helper: routes actions through the browser context.

    Instances are cheap — the route layer creates one per request.
    """

    @staticmethod
    def build_resume_summary(
        kind: str, target: str, extra: dict[str, Any]
    ) -> dict[str, Any]:
        """Compose the per-action summary written into the resume snapshot.

        Each action kind contributes one extra field on top of ``kind`` and
        ``target`` so the persisted snapshot has enough context to describe
        what the previous step did (used by ``cloak resume``).
        """
        summary: dict[str, Any] = {"kind": kind, "target": target}
        if kind in ("fill", "type"):
            summary["text"] = extra.get("text", "")
        elif kind in ("press", "keydown", "keyup"):
            summary["key"] = extra.get("key", "")
        elif kind == "scroll":
            summary["direction"] = extra.get("direction", "down")
        elif kind == "select":
            summary["value"] = extra.get("value", "")
        return summary

    async def execute(
        self,
        ctx: Any,
        kind: str,
        target: str,
        *,
        extra: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Run a single action, with one stale-ref auto-retry on miss.

        Returns ``(result, retried)`` so callers can flag it in the response.
        """
        try:
            result = await ctx.action(kind, target, **extra)
            return result, False
        except ElementNotFoundError:
            if target and target.isdigit():
                logger.info(
                    "stale_ref_retry",
                    kind=kind,
                    target=target,
                    reason="element_not_found",
                )
                await ctx.snapshot(mode="compact")
                result = await ctx.action(kind, target, **extra)
                return result, True
            raise

    async def execute_batch(
        self,
        ctx: Any,
        actions: list[dict[str, Any]],
        *,
        sleep_s: float = 0.0,
        settle_timeout: int | None = None,
    ) -> dict[str, Any]:
        """Execute a batch of actions with optional ``$N.path`` references.

        If no actions reference prior results, delegate to the context's
        native ``action_batch`` (faster path). Otherwise run our own loop
        that resolves references before each step.

        ``settle_timeout=None`` falls back to ``config.browser.batch_settle_timeout``.
        """
        if settle_timeout is None:
            from agentcloak.core.config import load_config

            _, cfg = load_config()
            settle_timeout = cfg.browser.batch_settle_timeout
        if not self.has_refs(actions):
            return await ctx.action_batch(
                actions, sleep=sleep_s, settle_timeout=settle_timeout
            )
        return await self._run_with_refs(
            ctx,
            actions,
            sleep_s=sleep_s,
            settle_timeout=settle_timeout,
        )

    # ------------------------------------------------------------------
    # $N reference resolution — public static so callers (and tests) can use
    # these helpers without instantiating the service.
    # ------------------------------------------------------------------

    @staticmethod
    def has_refs(actions: list[dict[str, Any]]) -> bool:
        for act in actions:
            for val in act.values():
                if isinstance(val, str) and _REF_PATTERN.match(val):
                    return True
        return False

    @staticmethod
    def resolve_refs(
        params: dict[str, Any], results: list[dict[str, Any]]
    ) -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        for key, val in params.items():
            if isinstance(val, str):
                m = _REF_PATTERN.match(val)
                if m:
                    idx = int(m.group(1))
                    path = m.group(2)
                    if 0 <= idx < len(results):
                        resolved[key] = ActionService.traverse(results[idx], path)
                        continue
            resolved[key] = val
        return resolved

    @staticmethod
    def traverse(obj: Any, path: str) -> Any:  # pyright: ignore[reportUnknownParameterType]
        cur: Any = obj
        for segment in path.split("."):
            if isinstance(cur, dict):
                cur = cur[segment]  # pyright: ignore[reportUnknownVariableType]
            else:
                raise KeyError(
                    f"Cannot traverse '{segment}' on {type(cur).__name__}"  # pyright: ignore[reportUnknownArgumentType]
                )
        return cur  # pyright: ignore[reportUnknownVariableType]

    async def _run_with_refs(
        self,
        ctx: Any,
        actions: list[dict[str, Any]],
        *,
        sleep_s: float,
        settle_timeout: int,
    ) -> dict[str, Any]:
        from agentcloak.core.config import load_config

        _, _cfg = load_config()
        _default_wait_timeout = _cfg.browser.action_timeout

        results: list[dict[str, Any]] = []
        total = len(actions)
        if total == 0:
            return {"results": [], "completed": 0, "total": 0}

        for i, act in enumerate(actions):
            try:
                resolved_act = self.resolve_refs(act, results)
            except (KeyError, IndexError, TypeError) as exc:
                results.append(
                    {
                        "ok": False,
                        "error": "ref_resolution_failed",
                        "hint": (
                            f"Failed to resolve $N reference in action {i}: {exc}"
                        ),
                        "action": (
                            "check that $N index is valid and path exists in result"
                        ),
                    }
                )
                return {
                    "results": results,
                    "completed": i,
                    "total": total,
                    "aborted_reason": "ref_resolution_failed",
                }

            kind = resolved_act.get("kind", resolved_act.get("action", ""))
            idx = resolved_act.get("index")
            target = str(idx) if idx is not None else resolved_act.get("target", "")
            extra = {
                k: v
                for k, v in resolved_act.items()
                if k not in ("kind", "action", "index", "target")
            }

            if kind == "wait":
                try:
                    result = await ctx.wait(
                        condition=extra.get("condition", "ms"),
                        value=str(extra.get("value", "1000")),
                        timeout=int(extra.get("timeout", _default_wait_timeout)),
                        state=str(extra.get("state", "visible")),
                    )
                except Exception as exc:
                    result = {"ok": False, "error": str(exc), "action": "wait"}
                results.append(result)
                continue

            try:
                result = await ctx.action(str(kind), str(target), **extra)
            except DialogBlockedError as exc:
                # Mirror base.action_batch: surface the dialog so the agent
                # can recover, but don't raise — partial batch results are
                # more useful than a single exception that loses prior steps.
                blocked = exc.to_dict()
                blocked["seq"] = getattr(ctx, "seq", 0)
                results.append(blocked)
                remaining = [
                    {
                        "index": j,
                        "kind": actions[j].get("kind", actions[j].get("action", "")),
                    }
                    for j in range(i + 1, total)
                ]
                return {
                    "results": results,
                    "completed": i,
                    "total": total,
                    "aborted_reason": "dialog_pending",
                    "dialog": exc.dialog,
                    "remaining": remaining,
                }
            results.append(result)

            if result.get("caused_navigation"):
                return {
                    "results": results,
                    "completed": i + 1,
                    "total": total,
                    "aborted_reason": "url_changed",
                }

            if sleep_s > 0 and i < total - 1:
                await asyncio.sleep(sleep_s)

        return {"results": results, "completed": total, "total": total}
