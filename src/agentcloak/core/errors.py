"""Three-field error envelope and exception hierarchy."""

from typing import Any

__all__ = [
    "AgentBrowserError",
    "BackendError",
    "BrowserTimeoutError",
    "DaemonConnectionError",
    "DebuggerPausedError",
    "DialogBlockedError",
    "ElementNotFoundError",
    "ImageDiffError",
    "NavigationError",
    "ProfileError",
    "SecurityError",
]


class AgentBrowserError(Exception):
    """Base exception carrying the three-field envelope.

    Subclasses can override :attr:`status_code` to tell the FastAPI exception
    handler which HTTP status to emit (defaults to ``400``). Carrying that
    mapping on the exception itself means routes never need ``try/except`` to
    translate domain errors into HTTP responses — they raise, the handler maps.
    """

    status_code: int = 400

    def __init__(self, *, error: str, hint: str, action: str) -> None:
        self.error = error
        self.hint = hint
        self.action = action
        super().__init__(hint)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": self.error,
            "hint": self.hint,
            "action": self.action,
        }


class NavigationError(AgentBrowserError):
    """URL navigation failures."""


class ElementNotFoundError(AgentBrowserError):
    """Target element not in selector_map."""


class BrowserTimeoutError(AgentBrowserError):
    """Operation exceeded timeout."""


class DaemonConnectionError(AgentBrowserError):
    """Cannot reach the daemon process."""


class ProfileError(AgentBrowserError):
    """Profile creation, loading, or validation failures."""


class SecurityError(AgentBrowserError):
    """IDPI security layer blocked an operation."""


class BackendError(AgentBrowserError):
    """Browser backend internal failure."""


class ImageDiffError(AgentBrowserError):
    """Local screenshot comparison failed."""


class DialogBlockedError(AgentBrowserError):
    """A pending browser dialog is blocking new actions.

    Carries the dialog metadata (``type``, ``message``, optional
    ``default_value``) so agents can decide how to handle it. Status code is
    409 (Conflict) because the action is valid but the page state prevents it.
    """

    status_code = 409

    def __init__(
        self,
        *,
        error: str,
        hint: str,
        action: str,
        dialog: dict[str, Any],
    ) -> None:
        super().__init__(error=error, hint=hint, action=action)
        self.dialog = dialog

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload["dialog"] = self.dialog
        return payload


class DebuggerPausedError(AgentBrowserError):
    """Page execution is paused at a breakpoint, blocking new page actions.

    Mirrors :class:`DialogBlockedError`: the requested action is valid but the
    page can't run it while execution is suspended in the debugger. Carries a
    compact ``paused_info`` summary (stop reason, top frame) so the agent knows
    where it's stuck. Status code 409 (Conflict) because page state — not the
    request — prevents the operation. Clear it with ``debugger resume`` or a
    ``debugger step`` command.
    """

    status_code = 409

    def __init__(
        self,
        *,
        error: str,
        hint: str,
        action: str,
        paused_info: dict[str, Any],
    ) -> None:
        super().__init__(error=error, hint=hint, action=action)
        self.paused_info = paused_info

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload["paused_info"] = self.paused_info
        return payload
