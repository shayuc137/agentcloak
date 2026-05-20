"""Pydantic models for streaming-capture routes (7b T2).

WebSocket frames and Server-Sent Events flow into bounded ring buffers keyed by
a monotonic ``seq`` (independent of the action counter) so
``GET /ws/messages?since=N`` and ``GET /sse/messages?since=N`` page through them
the same way ``/console`` and ``/network`` do. ``GET /ws/list`` reports the
tracked WebSocket connections (cleared on navigation, since open sockets die
with the page).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = [
    "SseEventModel",
    "SseMessagesResponse",
    "WsConnectionModel",
    "WsFrameModel",
    "WsListResponse",
    "WsMessagesResponse",
]


class WsConnectionModel(BaseModel):
    """One tracked WebSocket connection."""

    request_id: str = Field(description="CDP request id identifying the socket.")
    url: str = Field(description="WebSocket URL the connection was opened to.")
    status: str = Field(description="Connection status: 'open' or 'closed'.")
    created_at: float = Field(description="Unix timestamp when the socket opened.")
    closed_at: float | None = Field(
        None, description="Unix timestamp when it closed, if it has."
    )


class WsFrameModel(BaseModel):
    """A single WebSocket frame, sent or received."""

    seq: int = Field(description="Monotonic per-frame sequence number.")
    request_id: str = Field(description="CDP request id of the owning socket.")
    direction: str = Field(description="'sent' (client→server) or 'received'.")
    payload: str = Field(description="Frame payload (truncated past 4 KiB).")
    opcode: int = Field(description="WebSocket opcode (1=text, 2=binary, ...).")
    timestamp: float = Field(description="CDP monotonic timestamp of the frame.")


class SseEventModel(BaseModel):
    """A single Server-Sent Event (``EventSource`` message)."""

    seq: int = Field(description="Monotonic per-event sequence number.")
    request_id: str = Field(description="CDP request id of the EventSource.")
    event_name: str = Field(description="SSE event name (blank for default).")
    event_id: str = Field(description="SSE event id, if the server sent one.")
    data: str = Field(description="Event data payload (truncated past 4 KiB).")
    timestamp: float = Field(description="CDP monotonic timestamp of the event.")


class WsListResponse(BaseModel):
    """Tracked WebSocket connections."""

    connections: list[WsConnectionModel] = Field(
        description="WebSocket connections seen since the last navigation."
    )
    count: int = Field(description="Number of tracked connections.")


class WsMessagesResponse(BaseModel):
    """Buffered WebSocket frames newer than the requested ``since`` seq."""

    frames: list[WsFrameModel] = Field(description="WebSocket frames matching 'since'.")
    seq: int = Field(
        0, description="Highest WS frame seq seen; pass back as 'since' next time."
    )


class SseMessagesResponse(BaseModel):
    """Buffered SSE events newer than the requested ``since`` seq."""

    events: list[SseEventModel] = Field(description="SSE events matching 'since'.")
    seq: int = Field(
        0, description="Highest SSE event seq seen; pass back as 'since' next time."
    )
