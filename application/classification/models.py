from dataclasses import dataclass
from typing import Any


CLASSIFICATION_CATEGORIES = {
    "learning",
    "work",
    "entertainment",
    "unknown",
    "background_playback",
    "excluded",
}


@dataclass(frozen=True)
class ClassificationDecision:
    category: str
    activity_type: str
    confidence: float
    reason: str
    method: str

    def __post_init__(self) -> None:
        if self.category not in CLASSIFICATION_CATEGORIES:
            raise ValueError(f"Unsupported activity category: {self.category}")
        if not 0 <= self.confidence <= 1:
            raise ValueError("Classification confidence must be between 0 and 1.")

    @classmethod
    def from_dict(
        cls,
        value: dict[str, Any],
        method: str,
    ) -> "ClassificationDecision":
        return cls(
            category=str(value["category"]),
            activity_type=str(value.get("activity_type") or "unknown"),
            confidence=float(value["confidence"]),
            reason=str(value.get("reason") or ""),
            method=method,
        )
