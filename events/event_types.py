from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from models.state import PresenceState
from utils.time_utils import utc_now


@dataclass(frozen=True)
class Event:
    """Base event for runtime input and system notifications."""

    name: str
    timestamp: datetime = field(default_factory=utc_now)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PresenceDetected(Event):
    """External input: the desk sensor detected a person."""

    name: str = "PresenceDetected"


@dataclass(frozen=True)
class PresenceLost(Event):
    """External input: the desk sensor no longer detects a person."""

    name: str = "PresenceLost"


@dataclass(frozen=True)
class Shutdown(Event):
    """External input: stop the runtime gracefully."""

    name: str = "Shutdown"


@dataclass(frozen=True)
class SessionStarted(Event):
    """System event emitted after a session is created."""

    name: str = "SessionStarted"


@dataclass(frozen=True)
class SessionEnded(Event):
    """System event emitted after a session is closed."""

    name: str = "SessionEnded"


@dataclass(frozen=True)
class BreakStarted(Event):
    """System event emitted after a break is created."""

    name: str = "BreakStarted"


@dataclass(frozen=True)
class BreakEnded(Event):
    """System event emitted after a break is closed."""

    name: str = "BreakEnded"


@dataclass(frozen=True)
class StatisticsUpdated(Event):
    """System event emitted when statistics should be refreshed."""

    name: str = "StatisticsUpdated"


@dataclass(frozen=True)
class StateChanged(Event):
    """System event emitted when the state machine changes state."""

    old_state: PresenceState = PresenceState.IDLE
    new_state: PresenceState = PresenceState.IDLE
    name: str = "StateChanged"
