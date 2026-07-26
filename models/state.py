from enum import Enum


class PresenceState(str, Enum):
    """High-level lifecycle states for desk presence tracking."""

    IDLE = "Idle"
    WORKING = "Working"
    BREAK = "Break"
    FINISHED = "Finished"
