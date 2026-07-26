from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional


@dataclass(frozen=True)
class ContextEventRecord:
    """A persisted context capture from desktop, OS, or future AI modules."""

    id: int
    session_id: Optional[int]
    timestamp: datetime
    source: str
    payload: dict[str, Any]
    payload_json: str

    def as_dict(self) -> dict[str, Any]:
        """Return a structured dictionary for API consumers."""

        return {
            "id": self.id,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "source": self.source,
            "payload": self.payload,
            "payload_json": self.payload_json,
        }
