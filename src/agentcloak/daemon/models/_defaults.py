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

from agentcloak.core.config import BrowserConfig

__all__ = [
    "DEFAULT_ACTION_TIMEOUT",
    "DEFAULT_BATCH_SETTLE_TIMEOUT",
    "DEFAULT_MAX_RETURN_SIZE",
    "DEFAULT_NAVIGATE_TIMEOUT",
]


# Construct a fresh BrowserConfig — all browser tunables have hard-coded
# defaults on the dataclass, so no I/O happens. Pre-Phase-6d versions read
# from ``AgentcloakConfig`` directly; the sub-config refactor moved these
# fields to ``BrowserConfig`` so we follow them here.
_BROWSER_DEFAULTS = BrowserConfig()

DEFAULT_NAVIGATE_TIMEOUT: float = float(_BROWSER_DEFAULTS.navigation_timeout)
DEFAULT_ACTION_TIMEOUT: int = int(_BROWSER_DEFAULTS.action_timeout)
DEFAULT_BATCH_SETTLE_TIMEOUT: int = int(_BROWSER_DEFAULTS.batch_settle_timeout)
DEFAULT_MAX_RETURN_SIZE: int = int(_BROWSER_DEFAULTS.max_return_size)
