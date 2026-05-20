"""Pydantic request/response models for daemon HTTP API.

The single source of truth for daemon API schemas. Pydantic models drive:

* request body parsing and validation
* response serialization
* OpenAPI spec generation (the half-automatic CLI/MCP/Skill scaffolding
  in :mod:`scripts.generate_skill` reads ``app.openapi()``)

Grouped by feature area to mirror the route-group layout in
:mod:`agentcloak.daemon.routes`:

* :mod:`._envelope` — ``OkEnvelope``/``ErrorResponse`` shared by every route
* :mod:`.browser` — navigate, screenshot, snapshot, evaluate, network,
  action, action/batch, fetch
* :mod:`.capture` — capture/start, /stop, /status, /export, /analyze,
  /clear, /replay
* :mod:`.bridge` — bridge/claim, bridge/finalize, bridge/token/reset
* :mod:`.lifecycle` — health, shutdown, launch, resume, cdp/endpoint
* :mod:`.tabs` — tabs, tab/new, tab/close, tab/switch
* :mod:`.interaction` — dialog, wait, upload, frame, cookies
* :mod:`.spells` — spell/run, spell/list, profile/*

The flat re-export below preserves the original
``from agentcloak.daemon.models import XxxRequest`` import shape so
downstream consumers (scripts, tests, CLI/MCP client) keep working without
churn.
"""

from __future__ import annotations

from agentcloak.daemon.models._envelope import ErrorResponse, OkEnvelope
from agentcloak.daemon.models.bridge import (
    BridgeClaimRequest,
    BridgeFinalizeRequest,
    BridgeOpResponse,
    BridgeTokenResetResponse,
)
from agentcloak.daemon.models.browser import (
    ActionRequest,
    ActionResponse,
    BatchActionRequest,
    BatchActionResponse,
    EvaluateRequest,
    EvaluateResponse,
    FetchRequest,
    FetchResponse,
    NavigateRequest,
    NavigateResponse,
    NetworkResponse,
    ScreenshotResponse,
    SnapshotResponse,
)
from agentcloak.daemon.models.capture import (
    CaptureAnalyzeResponse,
    CaptureClearResponse,
    CaptureExportResponse,
    CaptureReplayRequest,
    CaptureReplayResponse,
    CaptureStatusResponse,
)
from agentcloak.daemon.models.clipboard import (
    ClipboardReadResponse,
    ClipboardWriteRequest,
    ClipboardWriteResponse,
)
from agentcloak.daemon.models.console import (
    ConsoleClearResponse,
    ConsoleEntryModel,
    ConsoleResponse,
)
from agentcloak.daemon.models.debugger import (
    BreakpointListResponse,
    BreakpointModel,
    BreakpointRemoveRequest,
    BreakpointSetRequest,
    BreakpointSetResponse,
    DebuggerEvaluateRequest,
    DebuggerEvaluateResponse,
    DebuggerOpResponse,
    DebuggerStateResponse,
    PausedInfoResponse,
    ScopeVariablesRequest,
    ScopeVariablesResponse,
    ScriptModel,
    ScriptsListResponse,
    ScriptSourceRequest,
    ScriptSourceResponse,
    SearchRequest,
    SearchResponse,
    SkipPausesRequest,
    StepRequest,
    XhrBreakpointRequest,
)
from agentcloak.daemon.models.download import (
    DownloadEntryModel,
    DownloadListResponse,
    DownloadResponse,
    DownloadUrlRequest,
    DownloadWaitRequest,
)
from agentcloak.daemon.models.emulation import HeadersRequest, HeadersResponse
from agentcloak.daemon.models.graphql import (
    GraphQLIntrospectRequest,
    GraphQLQueryRequest,
    GraphQLResponse,
)
from agentcloak.daemon.models.interaction import (
    CookieDeleteRequest,
    CookieDeleteResponse,
    CookiesClearResponse,
    CookieSetRequest,
    CookieSetResponse,
    CookiesExportRequest,
    CookiesExportResponse,
    CookiesImportRequest,
    CookiesImportResponse,
    DialogHandleRequest,
    DialogHandleResponse,
    DialogStatusResponse,
    FrameFocusRequest,
    FrameFocusResponse,
    FrameListResponse,
    UploadRequest,
    UploadResponse,
    WaitRequest,
    WaitResponse,
)
from agentcloak.daemon.models.lifecycle import (
    CDPEndpointResponse,
    HealthResponse,
    LaunchRequest,
    LaunchResponse,
    ResumeResponse,
    ShutdownResponse,
)
from agentcloak.daemon.models.pdf import PdfRequest, PdfResponse
from agentcloak.daemon.models.route import (
    RouteAddRequest,
    RouteListResponse,
    RouteOpResponse,
    RouteRemoveRequest,
)
from agentcloak.daemon.models.script import (
    ScriptAddRequest,
    ScriptAddResponse,
    ScriptListResponse,
    ScriptRemoveRequest,
    ScriptRemoveResponse,
)
from agentcloak.daemon.models.serve import (
    ServeStartRequest,
    ServeStatusResponse,
    ServeStopResponse,
)
from agentcloak.daemon.models.spells import (
    ProfileCreateFromCurrentRequest,
    ProfileCreateFromCurrentResponse,
    ProfileCreateRequest,
    ProfileCreateResponse,
    ProfileDeleteRequest,
    ProfileListResponse,
    SpellListResponse,
    SpellRunRequest,
    SpellRunResponse,
)
from agentcloak.daemon.models.storage import (
    StorageClearRequest,
    StorageDeleteRequest,
    StorageGetRequest,
    StorageResponse,
    StorageSetRequest,
)
from agentcloak.daemon.models.streaming import (
    SseEventModel,
    SseMessagesResponse,
    WsConnectionModel,
    WsFrameModel,
    WsListResponse,
    WsMessagesResponse,
)
from agentcloak.daemon.models.tabs import (
    TabCloseRequest,
    TabListResponse,
    TabNewRequest,
    TabOpResponse,
    TabSwitchRequest,
)

__all__ = [
    "ActionRequest",
    "ActionResponse",
    "BatchActionRequest",
    "BatchActionResponse",
    "BreakpointListResponse",
    "BreakpointModel",
    "BreakpointRemoveRequest",
    "BreakpointSetRequest",
    "BreakpointSetResponse",
    "BridgeClaimRequest",
    "BridgeFinalizeRequest",
    "BridgeOpResponse",
    "BridgeTokenResetResponse",
    "CDPEndpointResponse",
    "CaptureAnalyzeResponse",
    "CaptureClearResponse",
    "CaptureExportResponse",
    "CaptureReplayRequest",
    "CaptureReplayResponse",
    "CaptureStatusResponse",
    "ClipboardReadResponse",
    "ClipboardWriteRequest",
    "ClipboardWriteResponse",
    "ConsoleClearResponse",
    "ConsoleEntryModel",
    "ConsoleResponse",
    "CookieDeleteRequest",
    "CookieDeleteResponse",
    "CookieSetRequest",
    "CookieSetResponse",
    "CookiesClearResponse",
    "CookiesExportRequest",
    "CookiesExportResponse",
    "CookiesImportRequest",
    "CookiesImportResponse",
    "DebuggerEvaluateRequest",
    "DebuggerEvaluateResponse",
    "DebuggerOpResponse",
    "DebuggerStateResponse",
    "DialogHandleRequest",
    "DialogHandleResponse",
    "DialogStatusResponse",
    "DownloadEntryModel",
    "DownloadListResponse",
    "DownloadResponse",
    "DownloadUrlRequest",
    "DownloadWaitRequest",
    "ErrorResponse",
    "EvaluateRequest",
    "EvaluateResponse",
    "FetchRequest",
    "FetchResponse",
    "FrameFocusRequest",
    "FrameFocusResponse",
    "FrameListResponse",
    "GraphQLIntrospectRequest",
    "GraphQLQueryRequest",
    "GraphQLResponse",
    "HeadersRequest",
    "HeadersResponse",
    "HealthResponse",
    "LaunchRequest",
    "LaunchResponse",
    "NavigateRequest",
    "NavigateResponse",
    "NetworkResponse",
    "OkEnvelope",
    "PausedInfoResponse",
    "PdfRequest",
    "PdfResponse",
    "ProfileCreateFromCurrentRequest",
    "ProfileCreateFromCurrentResponse",
    "ProfileCreateRequest",
    "ProfileCreateResponse",
    "ProfileDeleteRequest",
    "ProfileListResponse",
    "ResumeResponse",
    "RouteAddRequest",
    "RouteListResponse",
    "RouteOpResponse",
    "RouteRemoveRequest",
    "ScopeVariablesRequest",
    "ScopeVariablesResponse",
    "ScreenshotResponse",
    "ScriptAddRequest",
    "ScriptAddResponse",
    "ScriptListResponse",
    "ScriptModel",
    "ScriptRemoveRequest",
    "ScriptRemoveResponse",
    "ScriptSourceRequest",
    "ScriptSourceResponse",
    "ScriptsListResponse",
    "SearchRequest",
    "SearchResponse",
    "ServeStartRequest",
    "ServeStatusResponse",
    "ServeStopResponse",
    "ShutdownResponse",
    "SkipPausesRequest",
    "SnapshotResponse",
    "SpellListResponse",
    "SpellRunRequest",
    "SpellRunResponse",
    "SseEventModel",
    "SseMessagesResponse",
    "StepRequest",
    "StorageClearRequest",
    "StorageDeleteRequest",
    "StorageGetRequest",
    "StorageResponse",
    "StorageSetRequest",
    "TabCloseRequest",
    "TabListResponse",
    "TabNewRequest",
    "TabOpResponse",
    "TabSwitchRequest",
    "UploadRequest",
    "UploadResponse",
    "WaitRequest",
    "WaitResponse",
    "WsConnectionModel",
    "WsFrameModel",
    "WsListResponse",
    "WsMessagesResponse",
    "XhrBreakpointRequest",
]
