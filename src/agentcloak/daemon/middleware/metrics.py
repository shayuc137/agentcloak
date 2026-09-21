"""Request metrics — counters surfaced through ``/health``.

The daemon is a long-running process, so operators occasionally want to know
"how busy is it / how long has it been up" without attaching a profiler.
:class:`MetricsState` is a tiny thread-safe counter pair that the metrics
middleware bumps on every request; :func:`DiagnosticService.health` reads it
back and the doctor/status text renderer prints a one-line summary.

We deliberately do *not* track browser memory here — a runaway browser
crashes and the daemon self-heals on the next request, and sampling RSS would
mean a psutil dependency or a ``/proc`` platform branch for marginal value.
``ps``/Task Manager covers that case.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

__all__ = ["MetricsState"]


@dataclass
class MetricsState:
    """Process-lifetime request counters.

    ``request_count`` is monotonic (every HTTP request that reaches the daemon,
    including ones rejected by the localhost gate). ``active_connections`` is
    the number of requests currently in flight — it goes up on entry and back
    down in a ``finally`` so an exception mid-handler can't leak the count.

    uvicorn runs a single-threaded event loop so the lock is defensive rather
    than load-bearing, but its cost is negligible and it keeps the counters
    correct if the app is ever driven from a thread pool (e.g. TestClient).
    """

    request_count: int = 0
    active_connections: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def enter(self) -> None:
        with self._lock:
            self.request_count += 1
            self.active_connections += 1

    def exit(self) -> None:
        with self._lock:
            # Guard against underflow if ``exit`` is somehow called more than
            # ``enter`` — the counter should never go negative.
            if self.active_connections > 0:
                self.active_connections -= 1
