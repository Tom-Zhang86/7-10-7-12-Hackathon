from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class SessionRecord:
    """A persisted work session.

    duration_seconds is net working time. Break durations are stored separately
    and subtracted from the session's wall-clock span.
    """

    id: int
    start_time: datetime
    end_time: Optional[datetime]
    duration_seconds: int
    break_count: int


@dataclass(frozen=True)
class BreakRecord:
    """A persisted away-from-desk period inside a session."""

    id: int
    session_id: int
    start_time: datetime
    end_time: Optional[datetime]
    duration_seconds: int
