"""StreamingMonitor — WebSocket frame + SSE event capture (Phase 7b T2).

Watches the CDP ``Network`` domain for streaming traffic that the ordinary
``network`` view can't see: WebSocket frames (``Network.webSocketFrameSent`` /
``...Received``) and Server-Sent Events (``Network.eventSourceMessageReceived``).
Each frame/event lands in a bounded ring buffer keyed by a monotonic ``seq`` so
``ws messages --since N`` / ``sse messages --since N`` page through them exactly
like ``console`` and ``network`` do.

The manager is lazily wired: nothing happens until an agent first asks for WS or
SSE data, at which point :meth:`ensure_listening` registers the event handlers
and enables the ``Network`` domain once. A session that never reverse-engineers
streaming traffic pays nothing — and we never force ``Network.enable`` on the
stealth backend's hot path.

All browser access goes through the base's thin CDP funnel
(``ctx._on_cdp_event`` / ``ctx._cdp_enable_domain``); the manager never touches a
backend session directly. The CDP event handlers are deliberately *synchronous*
because :meth:`BrowserContextBase._dispatch_cdp_event` invokes callbacks as plain
``cb(params)`` — they only append to in-memory deques, which needs no awaiting.
"""

# pyright: reportPrivateUsage=false
# StreamingMonitor is an intentional extension of BrowserContextBase: it reaches
# the browser exclusively through the base's thin CDP funnel
# (``_on_cdp_event`` / ``_cdp_enable_domain``), the documented collaboration
# (design decision D-Q3). Those names are "protected" to keep them off the public
# daemon surface, not to hide them from the managers the base itself constructs.

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentcloak.browser.base import BrowserContextBase

__all__ = ["SseEvent", "StreamingMonitor", "WsConnectionInfo", "WsFrame"]

# Bounded ring buffers — streaming endpoints can be chatty (chat apps, live
# dashboards), so cap memory the same way console does. Oldest frames/events
# drop off once the window fills; the seq cursor keeps paging honest.
_WS_BUFFER_SIZE = 1000
_SSE_BUFFER_SIZE = 1000

# WebSocket frames can carry large JSON payloads. Truncate so a single frame
# can't blow up the ``ws messages`` response; agents that need the full body can
# narrow with ``--since`` and read fewer frames.
_PAYLOAD_LIMIT = 4096


@dataclass
class WsConnectionInfo:
    """One tracked WebSocket connection (keyed by CDP ``requestId``)."""

    request_id: str
    url: str
    status: str = "open"  # "open" | "closed"
    created_at: float = 0.0
    closed_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "url": self.url,
            "status": self.status,
            "created_at": self.created_at,
            "closed_at": self.closed_at,
        }


@dataclass
class WsFrame:
    """A single WebSocket frame, sent or received."""

    seq: int
    request_id: str
    direction: str  # "sent" | "received"
    payload: str
    opcode: int = 1
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "request_id": self.request_id,
            "direction": self.direction,
            "payload": self.payload,
            "opcode": self.opcode,
            "timestamp": self.timestamp,
        }


@dataclass
class SseEvent:
    """A single Server-Sent Event (``EventSource`` message)."""

    seq: int
    request_id: str
    event_name: str
    event_id: str
    data: str
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "request_id": self.request_id,
            "event_name": self.event_name,
            "event_id": self.event_id,
            "data": self.data,
            "timestamp": self.timestamp,
        }


def _truncate(payload: str) -> str:
    """Cap a frame payload so one chatty frame can't dominate the response."""
    if len(payload) > _PAYLOAD_LIMIT:
        return payload[:_PAYLOAD_LIMIT] + "…[truncated]"
    return payload


class StreamingMonitor:
    """Capture WebSocket frames and SSE events via the CDP ``Network`` domain."""

    def __init__(self, ctx: BrowserContextBase) -> None:
        self._ctx = ctx
        # WS connection inventory keyed by CDP requestId; cleared on navigation.
        self._ws_connections: dict[str, WsConnectionInfo] = {}
        # Bounded frame/event ring buffers, each with its own monotonic seq —
        # the same shape console uses so ``--since`` paging is identical.
        self._ws_frames: deque[WsFrame] = deque(maxlen=_WS_BUFFER_SIZE)
        self._ws_seq: int = 0
        self._sse_events: deque[SseEvent] = deque(maxlen=_SSE_BUFFER_SIZE)
        self._sse_seq: int = 0
        self._listening: bool = False

    # ------------------------------------------------------------------
    # Lazy init
    # ------------------------------------------------------------------

    async def ensure_listening(self) -> None:
        """Register WS/SSE event handlers and enable ``Network`` (once).

        Idempotent: the public read methods call this before serving so the
        first ``ws``/``sse`` request transparently turns capture on. Subsequent
        calls are no-ops. ``_cdp_enable_domain`` is itself idempotent, so even
        if another manager (or the RemoteBridge capture path) already enabled
        ``Network``, double-enabling is safe.
        """
        if self._listening:
            return
        # Flip *before* the await so a concurrent read can't slip in and
        # re-register the handlers between the enable call and the flag set.
        self._listening = True
        self._ctx._on_cdp_event("Network.webSocketCreated", self._on_ws_created)
        self._ctx._on_cdp_event("Network.webSocketFrameSent", self._on_ws_frame_sent)
        self._ctx._on_cdp_event(
            "Network.webSocketFrameReceived", self._on_ws_frame_received
        )
        self._ctx._on_cdp_event("Network.webSocketClosed", self._on_ws_closed)
        self._ctx._on_cdp_event(
            "Network.eventSourceMessageReceived", self._on_sse_message
        )
        await self._ctx._cdp_enable_domain("Network")

    # ------------------------------------------------------------------
    # CDP event handlers (synchronous — see module docstring)
    # ------------------------------------------------------------------

    def _on_ws_created(self, params: dict[str, Any]) -> None:
        request_id = str(params.get("requestId", ""))
        if not request_id:
            return
        self._ws_connections[request_id] = WsConnectionInfo(
            request_id=request_id,
            url=str(params.get("url", "")),
            status="open",
            created_at=time.time(),
        )

    def _on_ws_frame_sent(self, params: dict[str, Any]) -> None:
        self._record_frame(params, direction="sent")

    def _on_ws_frame_received(self, params: dict[str, Any]) -> None:
        self._record_frame(params, direction="received")

    def _record_frame(self, params: dict[str, Any], *, direction: str) -> None:
        request_id = str(params.get("requestId", ""))
        # CDP nests the frame under ``response`` for both sent and received.
        # ``params`` is already ``dict[str, Any]`` so ``.get`` yields ``Any``;
        # default to an empty ``dict[str, Any]`` rather than a bare ``{}`` so
        # the field stays typed for the ``.get`` calls below.
        frame: dict[str, Any] = params.get("response") or {}
        self._ws_seq += 1
        self._ws_frames.append(
            WsFrame(
                seq=self._ws_seq,
                request_id=request_id,
                direction=direction,
                payload=_truncate(str(frame.get("payloadData", ""))),
                opcode=int(frame.get("opcode", 1)),
                timestamp=float(params.get("timestamp", 0.0)),
            )
        )

    def _on_ws_closed(self, params: dict[str, Any]) -> None:
        request_id = str(params.get("requestId", ""))
        conn = self._ws_connections.get(request_id)
        if conn is not None:
            conn.status = "closed"
            conn.closed_at = time.time()

    def _on_sse_message(self, params: dict[str, Any]) -> None:
        request_id = str(params.get("requestId", ""))
        self._sse_seq += 1
        self._sse_events.append(
            SseEvent(
                seq=self._sse_seq,
                request_id=request_id,
                event_name=str(params.get("eventName", "")),
                event_id=str(params.get("eventId", "")),
                data=_truncate(str(params.get("data", ""))),
                timestamp=float(params.get("timestamp", 0.0)),
            )
        )

    # ------------------------------------------------------------------
    # Reads (seq/since paging, mirrors console)
    # ------------------------------------------------------------------

    def ws_list(self) -> list[WsConnectionInfo]:
        """Return tracked WebSocket connections in creation order."""
        return list(self._ws_connections.values())

    def ws_messages(self, *, since: int = 0) -> tuple[list[WsFrame], int]:
        """Return frames with ``seq > since`` plus the highest seq seen."""
        frames = [f for f in self._ws_frames if f.seq > since]
        return frames, self._ws_seq

    def sse_messages(self, *, since: int = 0) -> tuple[list[SseEvent], int]:
        """Return SSE events with ``seq > since`` plus the highest seq seen."""
        events = [e for e in self._sse_events if e.seq > since]
        return events, self._sse_seq

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_navigated(self) -> None:
        """Drop the connection list — a navigation tears down open sockets.

        Frame/event history is *kept* (the seq cursor stays monotonic) so an
        agent can still read what flowed before the navigation; only the live
        connection inventory is cleared because those ``requestId``s are dead.
        """
        self._ws_connections.clear()

    async def on_tab_switched(self) -> None:
        """Re-enable ``Network`` on the now-active tab's CDP session.

        A switched-to tab has its own ``CDPSession`` with nothing enabled, so
        WS/SSE frames stop flowing until ``Network.enable`` is re-issued against
        it. We deliberately do *not* re-register the event handlers: they live
        on the ctx-level ``_cdp_event_handlers`` (registered once by
        :meth:`ensure_listening`) and already fan out to every session's
        forwarder, so re-registering would double-count every frame. We only
        clear the connection inventory (those ``requestId``s belong to the dead
        session) and re-enable the domain — mirroring
        :meth:`DebuggerManager.disable`'s ``_enabled_domains.discard`` so the
        idempotent :meth:`BrowserContextBase._cdp_enable_domain` actually
        re-issues on the new session. Frame/event history is kept so the seq
        cursor stays monotonic. A monitor that never started listening stays
        dormant — nothing to re-arm.
        """
        if not self._listening:
            return
        self._ws_connections.clear()
        self._ctx._enabled_domains.discard("Network")
        await self._ctx._cdp_enable_domain("Network")
