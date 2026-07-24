from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from typing import Any

from application.summary.models import SummaryGeneration
from models.state import PresenceState


@dataclass(frozen=True)
class StatusPresentation:
    label: str
    color: str


@dataclass(frozen=True)
class TimelineRow:
    time: str
    category: str
    detail: str


STATUS_PRESENTATIONS = {
    PresenceState.IDLE: StatusPresentation("空闲", "#737373"),
    PresenceState.WORKING: StatusPresentation("工作中", "#24734A"),
    PresenceState.BREAK: StatusPresentation("休息", "#A16207"),
    PresenceState.FINISHED: StatusPresentation("已结束", "#315A8A"),
}


def present_status(state: PresenceState | str) -> StatusPresentation:
    try:
        normalized = state if isinstance(state, PresenceState) else PresenceState(state)
    except ValueError:
        return StatusPresentation("未知", "#737373")
    return STATUS_PRESENTATIONS[normalized]


def format_duration(total_seconds: int) -> str:
    hours, remainder = divmod(max(int(total_seconds), 0), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def build_timeline_rows(
    timeline: list[dict[str, Any]],
    *,
    local_timezone: tzinfo | None = None,
    limit: int = 60,
) -> list[TimelineRow]:
    timezone_for_display = local_timezone or datetime.now().astimezone().tzinfo
    rows = [
        _timeline_row(item, timezone_for_display)
        for item in sorted(timeline, key=lambda value: value["timestamp"])
    ]
    return rows[-limit:]


def _timeline_row(item: dict[str, Any], local_timezone: tzinfo) -> TimelineRow:
    timestamp = item["timestamp"]
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    display_time = timestamp.astimezone(local_timezone).strftime("%H:%M")
    item_type = item.get("type")

    if item_type == "session":
        duration = int(item.get("duration_seconds") or 0)
        detail = "工作开始"
        if item.get("end_time") is not None:
            detail = f"工作结束 · {format_duration(duration)}"
        return TimelineRow(display_time, "工作", detail)

    if item_type == "break":
        duration = int(item.get("duration_seconds") or 0)
        detail = "离开座位"
        if item.get("end_time") is not None:
            detail = f"休息 · {format_duration(duration)}"
        return TimelineRow(display_time, "休息", detail)

    payload = item.get("payload") or {}
    app = str(payload.get("app") or "未知应用")
    title = str(payload.get("window_title") or "").strip()
    detail = app if not title else f"{app} — {title}"
    if len(detail) > 100:
        detail = detail[:97] + "..."
    return TimelineRow(display_time, "活动", detail)


def format_summary(generation: SummaryGeneration) -> str:
    summary = generation.summary
    sections = [
        summary.headline,
        "",
        summary.work_duration_summary,
        summary.focus_assessment,
    ]
    if summary.completed:
        sections.extend(["", "工作轨迹"])
        sections.extend(f"• {item}" for item in summary.completed)
    if summary.activity_insights:
        sections.extend(["", "证据边界"])
        sections.extend(f"• {item}" for item in summary.activity_insights)
    if summary.tomorrow_suggestions:
        sections.extend(["", "下一步"])
        sections.extend(f"• {item}" for item in summary.tomorrow_suggestions)
    sections.extend(["", summary.data_quality_note])
    return "\n".join(sections)
