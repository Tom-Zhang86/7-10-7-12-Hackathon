from dataclasses import dataclass


@dataclass(frozen=True)
class DailyStats:
    """Aggregated work metrics for a single calendar day."""

    total_work_seconds: int
    session_count: int
    break_count: int
    longest_focus_seconds: int

    def as_dict(self) -> dict[str, int]:
        """Return a simple dictionary for AI modules or API consumers."""

        return {
            "total_work_seconds": self.total_work_seconds,
            "session_count": self.session_count,
            "break_count": self.break_count,
            "longest_focus_seconds": self.longest_focus_seconds,
        }
