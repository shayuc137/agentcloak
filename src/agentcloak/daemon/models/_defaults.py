"""Schema-time defaults for Pydantic request models.

Pydantic emits ``Field(default=...)`` values into the OpenAPI schema at
class-definition time, without running ``default_factory``. So the request
models need concrete numbers available when they import.

Earlier versions called :func:`agentcloak.core.config.load_config` at module
import — which froze the OpenAPI schema against whatever the user's
``config.toml`` looked like the first time anything imported the daemon
package. Tests that monkey-patched the config wouldn't see their values, and
running the daemon under different configs required a process restart anyway.

The fix is simpler: surface the dataclass defaults directly from
:class:`AgentcloakConfig`. They're the same numbers the daemon would compute
on a fresh install with no overrides, which is the right thing to put in the
OpenAPI spec. Runtime values still come from
:func:`agentcloak.daemon.dependencies.get_config`, so an env-var or
``config.toml`` override continues to win at request time.
"""

from __future__ import annotations

from dataclasses import fields

from agentcloak.core.config import AgentcloakConfig

__all__ = [
    "DEFAULT_ACTION_TIMEOUT",
    "DEFAULT_BATCH_SETTLE_TIMEOUT",
    "DEFAULT_MAX_RETURN_SIZE",
    "DEFAULT_NAVIGATE_TIMEOUT",
]


def _default(name: str) -> object:
    """Pull a field's dataclass default without instantiating the class.

    ``AgentcloakConfig()`` would also work, but reaching into ``fields()``
    avoids running any ``default_factory`` we don't care about and makes the
    intent obvious.
    """
    for f in fields(AgentcloakConfig):
        if f.name == name:
            return f.default
    raise AttributeError(f"AgentcloakConfig has no field {name!r}")


DEFAULT_NAVIGATE_TIMEOUT: float = float(_default("navigation_timeout"))  # type: ignore[arg-type]
DEFAULT_ACTION_TIMEOUT: int = int(_default("action_timeout"))  # type: ignore[arg-type]
DEFAULT_BATCH_SETTLE_TIMEOUT: int = int(_default("batch_settle_timeout"))  # type: ignore[arg-type]
DEFAULT_MAX_RETURN_SIZE: int = int(_default("max_return_size"))  # type: ignore[arg-type]
