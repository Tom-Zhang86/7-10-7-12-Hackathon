"""Activity domain models (Master Spec section 4).

``ActivitySample`` deliberately has no field capable of holding an individual
key value, a stream of raw pointer coordinates, a full URL, screenshot
bytes, or a secret — the Master Spec requires these never be stored, so the
shape of this dataclass makes storing them structurally impossible rather
than merely discouraged by convention.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import uuid4
import math


class ActivityClassification(str, Enum):
    """Exactly one of these three values per observation period."""

    WORKING = "WORKING"
    NOT_WORKING = "NOT_WORKING"
    AMBIGUOUS = "AMBIGUOUS"


_COUNT_FIELDS = (
    "keypress_count",
    "mouse_move_count",
    "mouse_click_count",
    "scroll_count",
    "interface_switch_count",
)


@dataclass(frozen=True)
class ActivitySample:
    """One aggregate observation window.

    Only aggregate counts and short metadata strings are accepted: a
    keypress *count*, not the keys pressed; a mouse *move/click/scroll
    count*, not a trace of positions; a Chrome *domain*, not a full URL with
    query string.
    """

    observed_at: datetime
    duration_seconds: float
    keypress_count: int = 0
    mouse_move_count: int = 0
    mouse_click_count: int = 0
    scroll_count: int = 0
    interface_switch_count: int = 0
    active_application: str = ""
    window_title: str | None = None
    chrome_domain: str | None = None
    visible_filename: str | None = None
    sample_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        if (
            not isinstance(self.duration_seconds, (int, float))
            or isinstance(self.duration_seconds, bool)
            or not math.isfinite(self.duration_seconds)
            or self.duration_seconds <= 0
        ):
            raise ValueError("duration_seconds must be a positive finite number")
        for name in _COUNT_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not isinstance(self.active_application, str):
            raise TypeError("active_application must be a string")
        if self.window_title is not None and not isinstance(self.window_title, str):
            raise TypeError("window_title must be a string or None")
        if self.visible_filename is not None and not isinstance(
            self.visible_filename, str
        ):
            raise TypeError("visible_filename must be a string or None")
        if self.chrome_domain is not None:
            if not isinstance(self.chrome_domain, str) or not self.chrome_domain.strip():
                raise ValueError("chrome_domain must be a non-empty string or None")
            # Master Spec section 6/4: "Chrome domain only, never full URL."
            if "://" in self.chrome_domain or "/" in self.chrome_domain or "?" in self.chrome_domain:
                raise ValueError(
                    "chrome_domain must be a bare domain, never a full URL"
                )
        if not self.sample_id or not isinstance(self.sample_id, str):
            raise ValueError("sample_id must be a non-empty string")
