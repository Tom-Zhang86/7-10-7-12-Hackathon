from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from application.activity.models import (
    ActivitySegment,
    ActivitySpan,
    content_hash,
)
from utils.time_utils import utc_now


_SOURCE_PRIORITY = {
    "currentwindow": 10,
    "accessibility.context": 20,
    "web.tab.current": 30,
    "web.semantic": 30,
}


class ActivityFusionService:
    """Intersect watcher spans with presence sessions and break intervals."""

    def __init__(
        self,
        api: Any,
        store: Any,
        classifier: Any | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.api = api
        self.store = store
        self.classifier = classifier
        self.clock = clock

    def build_today(self) -> list[ActivitySegment]:
        return self.build_for_day(self.clock().date())

    def build_for_day(self, target_date: date) -> list[ActivitySegment]:
        day_start = datetime.combine(target_date, time.min, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        now = min(max(self.clock(), day_start), day_end)
        timeline = self.api.get_timeline_for_day(target_date)
        spans = self.store.list_spans(day_start, day_end)

        sessions = [item for item in timeline if item["type"] == "session"]
        breaks_by_session: dict[int, list[tuple[datetime, datetime]]] = (
            defaultdict(list)
        )
        for item in timeline:
            if item["type"] != "break":
                continue
            break_end = item.get("end_time") or now
            breaks_by_session[int(item["session_id"])].append(
                (item["start_time"], min(break_end, day_end))
            )

        generated: list[ActivitySegment] = []
        for session in sessions:
            session_id = int(session["session_id"])
            session_start = max(session["start_time"], day_start)
            session_end = min(session.get("end_time") or now, day_end)
            if session_end <= session_start:
                continue
            generated.extend(
                self._build_session(
                    session_id,
                    session_start,
                    session_end,
                    breaks_by_session.get(session_id, []),
                    spans,
                )
            )

        valid_hashes: set[str] = set()
        classified: list[ActivitySegment] = []
        for segment in generated:
            result = (
                self.classifier.classify(segment)
                if self.classifier is not None
                else segment
            )
            self.store.upsert_segment(result)
            valid_hashes.add(result.segment_hash)
            classified.append(result)
        self.store.delete_stale_segments(day_start, day_end, valid_hashes)
        return classified

    def category_seconds_today(self) -> dict[str, int]:
        segments = self.build_today()
        totals: dict[str, int] = defaultdict(int)
        for segment in segments:
            totals[segment.category] += int(segment.duration_seconds)
        return dict(totals)

    def _build_session(
        self,
        session_id: int,
        session_start: datetime,
        session_end: datetime,
        breaks: list[tuple[datetime, datetime]],
        spans: list[ActivitySpan],
    ) -> list[ActivitySegment]:
        relevant = [
            span
            for span in spans
            if span.start < session_end and span.end > session_start
        ]
        boundaries = {session_start, session_end}
        for span in relevant:
            boundaries.add(max(span.start, session_start))
            boundaries.add(min(span.end, session_end))
        for break_start, break_end in breaks:
            if break_start < session_end and break_end > session_start:
                boundaries.add(max(break_start, session_start))
                boundaries.add(min(break_end, session_end))

        ordered = sorted(boundaries)
        segments: list[ActivitySegment] = []
        for start, end in zip(ordered, ordered[1:]):
            if end <= start:
                continue
            active = [
                span
                for span in relevant
                if span.start < end and span.end > start
            ]
            if not active:
                continue
            presence_state = (
                "away"
                if any(
                    break_start < end and break_end > start
                    for break_start, break_end in breaks
                )
                else "present"
            )
            segments.append(
                self._make_segment(
                    session_id,
                    start,
                    end,
                    presence_state,
                    active,
                )
            )
        return segments

    @staticmethod
    def _make_segment(
        session_id: int,
        start: datetime,
        end: datetime,
        presence_state: str,
        active: list[ActivitySpan],
    ) -> ActivitySegment:
        ordered = sorted(
            active,
            key=lambda span: _SOURCE_PRIORITY.get(span.event_type, 0),
        )
        evidence: dict[str, Any] = {}
        for span in ordered:
            evidence.update(span.data)
        evidence["sources"] = [span.source for span in ordered]
        event_ids = tuple(
            sorted(
                span.event_id
                for span in ordered
                if span.event_id is not None
            )
        )
        media = evidence.get("media")
        media_playing = bool(
            isinstance(media, dict) and media.get("playing")
        ) or bool(evidence.get("audible"))
        if media_playing:
            activity_type = "media"
        elif evidence.get("url"):
            activity_type = "browser"
        elif evidence.get("project") or evidence.get("file"):
            activity_type = "editor"
        else:
            activity_type = "application"
        category = (
            "background_playback"
            if presence_state == "away" and media_playing
            else "unknown"
        )
        confidence = 1.0 if category == "background_playback" else 0.0
        identity = content_hash(
            {
                "session_id": session_id,
                "start": start.isoformat(),
                "presence": presence_state,
                "event_ids": event_ids,
                "event_hashes": [span.content_hash for span in ordered],
            }
        )
        return ActivitySegment(
            session_id=session_id,
            start=start,
            end=end,
            presence_state=presence_state,
            activity_type=activity_type,
            category=category,
            confidence=confidence,
            evidence=evidence,
            source_event_ids=event_ids,
            classifier_version="unclassified",
            segment_hash=identity,
        )
