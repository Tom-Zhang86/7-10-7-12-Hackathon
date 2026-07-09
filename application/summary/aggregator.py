from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


class DailyDataAggregator:
    """Build compact, LLM-ready daily data from B's public query APIs."""

    def __init__(self, api: Any, max_sample_gap_seconds: int = 120) -> None:
        self.api = api
        self.max_sample_gap_seconds = max_sample_gap_seconds

    def build_today(self) -> dict[str, Any]:
        return self._build(
            stats=self.api.get_today_stats(),
            timeline=self.api.get_today_timeline(),
        )

    def build_for_day(self, target_date: date) -> dict[str, Any]:
        return self._build(
            stats=self.api.get_stats_for_day(target_date),
            timeline=self.api.get_timeline_for_day(target_date),
        )

    def _build(
        self,
        stats: dict[str, Any],
        timeline: list[dict[str, Any]],
    ) -> dict[str, Any]:
        sessions = [
            self._serialize_interval(item)
            for item in timeline
            if item["type"] == "session"
        ]
        breaks = [
            self._serialize_interval(item)
            for item in timeline
            if item["type"] == "break"
        ]
        context_events = [
            item for item in timeline if item["type"] == "context_event"
        ]
        activity_blocks = self._activity_blocks(context_events)

        app_seconds: dict[str, int] = defaultdict(int)
        for block in activity_blocks:
            app_seconds[block["app"]] += block["estimated_seconds"]

        frequent_apps = [
            {"app": app, "estimated_seconds": seconds}
            for app, seconds in sorted(
                app_seconds.items(),
                key=lambda item: (-item[1], item[0].lower()),
            )
        ]

        return {
            "stats": dict(stats),
            "sessions": sessions,
            "breaks": breaks,
            "activity_blocks": activity_blocks,
            "frequent_apps": frequent_apps,
            "context_event_count": len(context_events),
        }

    @staticmethod
    def _serialize_interval(item: dict[str, Any]) -> dict[str, Any]:
        return {
            key: _iso(value)
            for key, value in item.items()
            if key != "timestamp"
        }

    def _activity_blocks(
        self,
        events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        ordered = sorted(events, key=lambda item: item["timestamp"])
        blocks: list[dict[str, Any]] = []

        for index, item in enumerate(ordered):
            start = item["timestamp"]
            next_start = (
                ordered[index + 1]["timestamp"]
                if index + 1 < len(ordered)
                else start
            )
            inferred_end = min(
                next_start,
                start + timedelta(seconds=self.max_sample_gap_seconds),
            )
            payload = item.get("payload", {})
            app = str(payload.get("app") or "Unknown")
            title = str(payload.get("window_title") or "")
            duration = max(int((inferred_end - start).total_seconds()), 0)

            if (
                blocks
                and blocks[-1]["app"] == app
                and blocks[-1]["window_title"] == title
                and blocks[-1]["end"] == start.isoformat()
            ):
                blocks[-1]["end"] = inferred_end.isoformat()
                blocks[-1]["estimated_seconds"] += duration
                continue

            blocks.append(
                {
                    "start": start.isoformat(),
                    "end": inferred_end.isoformat(),
                    "app": app,
                    "window_title": title,
                    "estimated_seconds": duration,
                    "session_id": item.get("session_id"),
                }
            )

        return blocks
