"""Presence and wearable domain models (Master Spec section 6).

These are pure, hardware-independent contracts. Adapters (``adapters/radar.py``,
``adapters/wearable.py``) produce these objects; this module has no I/O and
no dependency on any transport.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class RadarState(str, Enum):
    """Desk radar answer (Master Spec section 6)."""

    PERSON_PRESENT = "PERSON_PRESENT"
    PERSON_ABSENT = "PERSON_ABSENT"
    UNKNOWN = "UNKNOWN"


class WearableProximityState(str, Enum):
    """Wearable proximity answer (Master Spec section 6)."""

    OWNER_NEAR = "OWNER_NEAR"
    OWNER_AWAY = "OWNER_AWAY"
    UNKNOWN = "UNKNOWN"


class WearableButton(str, Enum):
    """The only values a wearable button press may report (Master Spec
    section 6). There is no "raw packet" representation anywhere in this
    project — a malformed or out-of-family value fails to construct at all
    rather than being stored and interpreted later."""

    A = "A"
    B = "B"
    C = "C"
    D = "D"
    YES = "YES"
    NO = "NO"


@dataclass(frozen=True)
class RadarSample:
    """One radar observation. UNKNOWN is a real, first-class value here —
    callers must never coerce it to PERSON_ABSENT or PERSON_PRESENT."""

    state: RadarState
    observed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.state, RadarState):
            raise TypeError("state must be a RadarState")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")


@dataclass(frozen=True)
class WearableProximitySample:
    """One wearable proximity observation."""

    state: WearableProximityState
    observed_at: datetime
    device_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.state, WearableProximityState):
            raise TypeError("state must be a WearableProximityState")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        if not self.device_id or not self.device_id.strip():
            raise ValueError("device_id must be a non-empty string")


@dataclass(frozen=True)
class WearableAnswer:
    """Wearable answer contract (Master Spec section 6 — schema preserved):

    ``{"device_id": "", "question_id": "", "button": "", "timestamp": ""}``

    Constructing this object with an out-of-family button value, or an
    empty/whitespace device_id or question_id, raises immediately — this is
    the "malformed packet" rejection boundary. No raw BLE bytes or device
    secrets are represented anywhere in this contract, by construction: the
    only fields that exist are the four above.
    """

    device_id: str
    question_id: str
    button: WearableButton
    timestamp: datetime

    def __post_init__(self) -> None:
        if not self.device_id or not self.device_id.strip():
            raise ValueError("device_id must be a non-empty string")
        if not self.question_id or not self.question_id.strip():
            raise ValueError("question_id must be a non-empty string")
        if not isinstance(self.button, WearableButton):
            raise TypeError("button must be a WearableButton")
        if self.timestamp.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")

    def as_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "question_id": self.question_id,
            "button": self.button.value,
            "timestamp": self.timestamp.isoformat(),
        }
