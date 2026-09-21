"""Request scheduling policy shared by middleware and route coverage checks."""

from enum import Enum


class Scheduling(Enum):
    BYPASS = "bypass"
    RELEASE = "release"
    OBSERVE = "observe"
    LIFECYCLE = "lifecycle"
    SERIAL = "serial"


PATHS: dict[Scheduling, frozenset[str]] = {
    Scheduling.BYPASS: frozenset(
        {
            "/docs",
            "/docs/oauth2-redirect",
            "/health",
            "/openapi.json",
            "/redoc",
            "/session/list",
            "/shutdown",
        }
    ),
    Scheduling.RELEASE: frozenset(
        {
            "/network",
            "/record/status",
            "/route/list",
            "/route/release",
        }
    ),
    Scheduling.OBSERVE: frozenset(
        {
            "/screenshot",
            "/snapshot",
        }
    ),
    Scheduling.LIFECYCLE: frozenset(
        {
            "/launch",
            "/session/close",
            "/tab/close",
            "/tab/switch",
            "/viewport",
        }
    ),
    Scheduling.SERIAL: frozenset(
        {
            "/action",
            "/action/batch",
            "/bridge/claim",
            "/bridge/finalize",
            "/bridge/token/reset",
            "/capture/analyze",
            "/capture/clear",
            "/capture/export",
            "/capture/replay",
            "/capture/start",
            "/record/start",
            "/record/stop",
            "/capture/status",
            "/capture/stop",
            "/cdp/endpoint",
            "/cdp/send",
            "/clipboard/read",
            "/clipboard/write",
            "/console",
            "/console/clear",
            "/cookies/clear",
            "/cookies/delete",
            "/cookies/export",
            "/cookies/import",
            "/cookies/set",
            "/debugger/breakpoint/list",
            "/debugger/breakpoint/remove",
            "/debugger/breakpoint/set",
            "/debugger/disable",
            "/debugger/enable",
            "/debugger/evaluate",
            "/debugger/paused-info",
            "/debugger/resume",
            "/debugger/scope-variables",
            "/debugger/script-source",
            "/debugger/scripts",
            "/debugger/search",
            "/debugger/skip-pauses",
            "/debugger/step",
            "/debugger/xhr-breakpoint/remove",
            "/debugger/xhr-breakpoint/set",
            "/dialog/handle",
            "/dialog/status",
            "/download/list",
            "/download/url",
            "/download/wait",
            "/download/wait-click",
            "/emulation",
            "/emulation/headers",
            "/evaluate",
            "/fetch",
            "/frame/focus",
            "/frame/list",
            "/graphql/introspect",
            "/graphql/query",
            "/hide/add",
            "/hide/list",
            "/hide/remove",
            "/navigate",
            "/pdf",
            "/performance/metrics",
            "/profile/create",
            "/profile/create-from-current",
            "/profile/delete",
            "/profile/list",
            "/profiler/coverage/get",
            "/profiler/coverage/start",
            "/profiler/coverage/stop",
            "/profiler/cpu/start",
            "/profiler/cpu/stop",
            "/profiler/heap/snapshot",
            "/resume",
            "/route/add",
            "/route/remove",
            "/script/add",
            "/script/list",
            "/script/remove",
            "/serve/start",
            "/serve/status",
            "/serve/stop",
            "/sourcemap/get",
            "/sourcemap/list",
            "/sourcemap/lookup",
            "/sourcemap/source-content",
            "/sourcemap/sources",
            "/spell/list",
            "/spell/run",
            "/sse/messages",
            "/storage/clear",
            "/storage/delete",
            "/storage/get",
            "/storage/set",
            "/tab/new",
            "/tabs",
            "/upload",
            "/wait",
            "/ws/list",
            "/ws/messages",
        }
    ),
}

POLICY = {path: policy for policy, paths in PATHS.items() for path in paths}


def scheduling_for(path: str) -> Scheduling:
    # Unknown URLs still serialize; the route coverage test guards new endpoints.
    return POLICY.get(path, Scheduling.SERIAL)
