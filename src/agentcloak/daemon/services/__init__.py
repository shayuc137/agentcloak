"""Service layer for the daemon.

Each service owns a slice of business logic that the FastAPI route handlers
hand off to. Routes become thin: parse Pydantic body → call service → return
result. Tests can exercise services in isolation without spinning up FastAPI.
"""

from __future__ import annotations

from agentcloak.daemon.services.action_service import ActionService
from agentcloak.daemon.services.bridge_service import BridgeService, BridgeWSAdapter
from agentcloak.daemon.services.capture_service import CaptureService
from agentcloak.daemon.services.diagnostic_service import DiagnosticService
from agentcloak.daemon.services.file_server import FileServer
from agentcloak.daemon.services.profile_service import ProfileService
from agentcloak.daemon.services.profiler_service import ProfilerService
from agentcloak.daemon.services.session_manager import SessionManager, SessionSlot
from agentcloak.daemon.services.snapshot_service import SnapshotService

__all__ = [
    "ActionService",
    "BridgeService",
    "BridgeWSAdapter",
    "CaptureService",
    "DiagnosticService",
    "FileServer",
    "ProfileService",
    "ProfilerService",
    "SessionManager",
    "SessionSlot",
    "SnapshotService",
]
