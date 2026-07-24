from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


class DailyDataAggregator:
    """Build compact, LLM-ready daily data from B's public query APIs."""

    def __init__(
        self,
        api: Any,
        max_sample_gap_seconds: int = 120,
    ) -> None:
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
            item
            for item in timeline
            if item["type"] == "context_event"
            and item.get("source") != "attention_window"
        ]
        attention_events = [
            item
            for item in timeline
            if item["type"] == "context_event"
            and item.get("source") == "attention_window"
        ]
        activity_blocks = self._activity_blocks(context_events)
        attention_summary = self._attention_summary(attention_events)
        attention_windows = self._attention_windows(attention_events)
        behavior_metrics = self._behavior_metrics(
            stats=stats,
            attention_windows=attention_windows,
        )

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
            "attention_summary": attention_summary,
            "attention_windows": attention_windows,
            "behavior_metrics": behavior_metrics,
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
            browser_title = str(payload.get("browser_title") or "")
            browser_url = str(payload.get("browser_url") or "")
            duration = max(int((inferred_end - start).total_seconds()), 0)

            if (
                blocks
                and blocks[-1]["app"] == app
                and blocks[-1]["window_title"] == title
                and blocks[-1]["browser_title"] == browser_title
                and blocks[-1]["browser_url"] == browser_url
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
                    "browser_title": browser_title,
                    "browser_url": browser_url,
                    "estimated_seconds": duration,
                    "session_id": item.get("session_id"),
                }
            )

        return blocks

    @staticmethod
    def _attention_summary(
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        state_seconds: dict[str, float] = defaultdict(float)
        mouse_states: dict[str, int] = defaultdict(int)
        keyboard_states: dict[str, int] = defaultdict(int)
        content_states: dict[str, int] = defaultdict(int)
        total_keypresses = 0
        total_clicks = 0
        total_scrolls = 0
        for item in events:
            payload = item.get("payload") or {}
            duration = max(float(payload.get("duration_seconds") or 0), 0)
            attention = str(payload.get("attention_state") or "UNCERTAIN")
            state_seconds[attention] += duration
            mouse_states[str(payload.get("mouse_state") or "NO_ACTIVITY")] += 1
            keyboard_states[
                str(payload.get("keyboard_state") or "NO_TYPING")
            ] += 1
            content_states[
                str(payload.get("content_state") or "AMBIGUOUS")
            ] += 1
            total_keypresses += int(payload.get("keypress_count") or 0)
            total_clicks += int(payload.get("mouse_click_count") or 0)
            total_scrolls += int(payload.get("scrolling_count") or 0)
        return {
            "window_count": len(events),
            "attention_seconds": {
                key: round(value, 2)
                for key, value in sorted(state_seconds.items())
            },
            "mouse_state_windows": dict(sorted(mouse_states.items())),
            "keyboard_state_windows": dict(sorted(keyboard_states.items())),
            "content_state_windows": dict(sorted(content_states.items())),
            "aggregate_counts": {
                "keypresses": total_keypresses,
                "mouse_clicks": total_clicks,
                "scroll_events": total_scrolls,
            },
            "privacy_note": (
                "Only aggregate counts and classifications were recorded; "
                "no actual keys or pointer coordinates were stored."
            ),
        }

    @staticmethod
    def _attention_windows(
        events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Expose compact anonymous evidence for the AI to assess itself."""

        allowed = (
            "duration_seconds",
            "mouse_move_count",
            "mouse_distance",
            "mouse_click_count",
            "scrolling_count",
            "keypress_count",
            "typing_burst_count",
            "average_typing_speed",
            "longest_no_typing_period",
            "seconds_on_interface",
            "interface_switch_count",
            "presence_sensor",
            "mouse_state",
            "keyboard_state",
            "content_state",
            "switching_pattern",
            "attention_state",
        )
        windows = []
        for item in events:
            payload = item.get("payload") or {}
            window = {
                "timestamp": _iso(item.get("timestamp")),
                **{key: payload[key] for key in allowed if key in payload},
            }
            windows.append(window)
        return windows

    @staticmethod
    def _behavior_metrics(
        stats: dict[str, Any],
        attention_windows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Compute user-facing durations before the LLM writes the report."""

        focused_states = {"FOCUSED_ACTIVE", "FOCUSED_PASSIVE"}
        runs: dict[str, list[float]] = {"focused": [], "distracted": []}
        recovery_times: list[float] = []
        current_kind: str | None = None
        current_seconds = 0.0
        recovering_seconds: float | None = None

        def finish_run() -> None:
            nonlocal current_kind, current_seconds
            if current_kind is not None and current_seconds > 0:
                runs[current_kind].append(current_seconds)
            current_kind = None
            current_seconds = 0.0

        for window in sorted(
            attention_windows,
            key=lambda item: str(item.get("timestamp") or ""),
        ):
            state = str(window.get("attention_state") or "UNCERTAIN")
            kind = (
                "focused"
                if state in focused_states
                else "distracted"
                if state == "DISTRACTED"
                else None
            )
            duration = max(float(window.get("duration_seconds") or 0), 0)
            if recovering_seconds is not None:
                if kind == "focused":
                    recovery_times.append(recovering_seconds)
                    recovering_seconds = None
                else:
                    recovering_seconds += duration
            elif kind == "distracted":
                recovering_seconds = duration
            if kind != current_kind:
                finish_run()
                current_kind = kind
            if kind is not None:
                current_seconds += duration
        finish_run()

        def average(values: list[float]) -> int:
            return round(sum(values) / len(values)) if values else 0

        work_seconds = max(int(stats.get("total_work_seconds") or 0), 0)
        sessions = max(int(stats.get("session_count") or 0), 0)
        focused_seconds = round(sum(runs["focused"]))
        distracted_seconds = round(sum(runs["distracted"]))
        classified_seconds = focused_seconds + distracted_seconds
        return {
            "effective_focus_seconds": focused_seconds,
            "focus_rate_percent": (
                round(focused_seconds * 100 / classified_seconds)
                if classified_seconds
                else 0
            ),
            "average_concentration_seconds": average(runs["focused"]),
            "average_distraction_seconds": average(runs["distracted"]),
            "longest_concentration_seconds": (
                round(max(runs["focused"])) if runs["focused"] else 0
            ),
            "longest_continuous_work_seconds": max(
                int(stats.get("longest_focus_seconds") or 0),
                0,
            ),
            "average_work_session_seconds": (
                round(work_seconds / sessions) if sessions else 0
            ),
            "focus_period_count": len(runs["focused"]),
            "distraction_period_count": len(runs["distracted"]),
            "average_recovery_seconds": average(recovery_times),
            "recovered_distraction_count": len(recovery_times),
            "method": (
                "Concentration and distraction averages are computed from "
                "contiguous classified attention windows; continuous work "
                "comes from recorded work sessions."
            ),
        }
