from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any


SUMMARY_FIELDS = (
    "headline",
    "completed",
    "work_duration_summary",
    "focus_assessment",
    "activity_insights",
    "tomorrow_suggestions",
    "data_quality_note",
)


@dataclass(frozen=True)
class DailySummary:
    """Validated summary content consumed by the future demo UI."""

    headline: str
    completed: list[str]
    work_duration_summary: str
    focus_assessment: str
    activity_insights: list[str]
    tomorrow_suggestions: list[str]
    data_quality_note: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DailySummary":
        if not isinstance(value, dict):
            raise TypeError("Daily summary must be a dictionary.")

        missing = [field for field in SUMMARY_FIELDS if field not in value]
        if missing:
            raise ValueError(
                f"Daily summary is missing fields: {', '.join(missing)}"
            )

        string_fields = (
            "headline",
            "work_duration_summary",
            "focus_assessment",
            "data_quality_note",
        )
        list_fields = (
            "completed",
            "activity_insights",
            "tomorrow_suggestions",
        )

        for field in string_fields:
            if not isinstance(value[field], str):
                raise TypeError(f"{field} must be a string.")
        for field in list_fields:
            items = value[field]
            if not isinstance(items, list) or not all(
                isinstance(item, str) for item in items
            ):
                raise TypeError(f"{field} must be a list of strings.")

        return cls(**{field: value[field] for field in SUMMARY_FIELDS})

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SummaryGeneration:
    """One persisted result from an explicit manual generation request."""

    target_date: date
    generated_at: datetime
    source: str
    summary: DailySummary
    warning: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.target_date.isoformat(),
            "generated_at": self.generated_at.isoformat(),
            "source": self.source,
            "warning": self.warning,
            "summary": self.summary.as_dict(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SummaryGeneration":
        return cls(
            target_date=date.fromisoformat(value["date"]),
            generated_at=datetime.fromisoformat(value["generated_at"]),
            source=str(value["source"]),
            warning=value.get("warning"),
            summary=DailySummary.from_dict(value["summary"]),
        )
