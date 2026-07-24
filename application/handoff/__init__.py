"""Presence-driven A2A task handoff."""

from application.handoff.a2a_client import A2AHandoffClient
from application.handoff.models import (
    A2AResult,
    HandoffInput,
    HandoffRecord,
    HandoffStatus,
    TaskCapsule,
)
from application.handoff.orchestrator import HandoffOrchestrator
from application.handoff.store import HandoffStore

__all__ = [
    "A2AHandoffClient",
    "A2AResult",
    "HandoffInput",
    "HandoffOrchestrator",
    "HandoffRecord",
    "HandoffStatus",
    "HandoffStore",
    "TaskCapsule",
]
