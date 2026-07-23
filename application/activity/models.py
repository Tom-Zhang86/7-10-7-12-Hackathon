from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Any


def content_hash(data: dict[str, Any]) -> str:
    """Return a stable, compact identity for one watcher payload."""

    encoded = json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ActivityObservation:
    """One instantaneous observation produced by an internal watcher."""

    timestamp: datetime
    bucket_id: str
    event_type: str
    source: str
    data: dict[str, Any]

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("Activity timestamps must be timezone-aware.")
        if not self.bucket_id or not self.event_type or not self.source:
            raise ValueError("Activity identity fields must be non-empty.")
        if not isinstance(self.data, dict):
            raise TypeError("Activity data must be a dictionary.")

    @property
    def content_hash(self) -> str:
        return content_hash(self.data)


@dataclass(frozen=True)
class ActivitySpan:
    """A compact time span created by merging watcher heartbeats."""

    start: datetime
    end: datetime
    bucket_id: str
    event_type: str
    source: str
    data: dict[str, Any]
    content_hash: str
    event_id: int | None = None

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Activity span timestamps must be timezone-aware.")
        if self.end < self.start:
            raise ValueError("Activity span end cannot precede its start.")

    @property
    def duration_seconds(self) -> float:
        return max((self.end - self.start).total_seconds(), 0.0)


@dataclass(frozen=True)
class ActivitySegment:
    """A presence-aware activity interval ready for classification."""

    session_id: int | None
    start: datetime
    end: datetime
    presence_state: str
    activity_type: str
    category: str
    confidence: float
    evidence: dict[str, Any]
    source_event_ids: tuple[int, ...]
    classifier_version: str
    user_corrected: bool = False
    segment_hash: str = ""

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("Activity segment end cannot precede its start.")
        if not 0 <= self.confidence <= 1:
            raise ValueError("Activity confidence must be between 0 and 1.")

    @property
    def duration_seconds(self) -> float:
        return max((self.end - self.start).total_seconds(), 0.0)
