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
from agentcloak.daemon.models.interaction import (
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
    "CookiesExportRequest",
    "CookiesExportResponse",
    "CookiesImportRequest",
    "CookiesImportResponse",
    "DialogHandleRequest",
    "DialogHandleResponse",
    "DialogStatusResponse",
    "ErrorResponse",
    "EvaluateRequest",
    "EvaluateResponse",
    "FetchRequest",
    "FetchResponse",
    "FrameFocusRequest",
    "FrameFocusResponse",
    "FrameListResponse",
    "HealthResponse",
    "LaunchRequest",
    "LaunchResponse",
    "NavigateRequest",
    "NavigateResponse",
    "NetworkResponse",
    "OkEnvelope",
    "ProfileCreateFromCurrentRequest",
    "ProfileCreateFromCurrentResponse",
    "ProfileCreateRequest",
    "ProfileCreateResponse",
    "ProfileDeleteRequest",
    "ProfileListResponse",
    "ResumeResponse",
    "ScreenshotResponse",
    "ShutdownResponse",
    "SnapshotResponse",
    "SpellListResponse",
    "SpellRunRequest",
    "SpellRunResponse",
    "TabCloseRequest",
    "TabListResponse",
    "TabNewRequest",
    "TabOpResponse",
    "TabSwitchRequest",
    "UploadRequest",
    "UploadResponse",
    "WaitRequest",
    "WaitResponse",
]
