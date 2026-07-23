from dataclasses import dataclass
from datetime import datetime, timedelta

from application.activity.models import ActivityObservation, ActivitySpan


@dataclass
class _OpenHeartbeat:
    observation: ActivityObservation
    start: datetime
    last_seen: datetime

    def as_span(self, end: datetime) -> ActivitySpan:
        return ActivitySpan(
            start=self.start,
            end=max(end, self.start),
            bucket_id=self.observation.bucket_id,
            event_type=self.observation.event_type,
            source=self.observation.source,
            data=dict(self.observation.data),
            content_hash=self.observation.content_hash,
        )


class HeartbeatReducer:
    """Merge adjacent equal watcher observations into durable time spans."""

    def __init__(self, pulsetime_seconds: float = 10.0) -> None:
        if pulsetime_seconds <= 0:
            raise ValueError("pulsetime_seconds must be positive.")
        self.pulsetime_seconds = pulsetime_seconds
        self._open: dict[str, _OpenHeartbeat] = {}

    def ingest(self, observation: ActivityObservation) -> list[ActivitySpan]:
        current = self._open.get(observation.bucket_id)
        if current is None:
            self._open[observation.bucket_id] = _OpenHeartbeat(
                observation=observation,
                start=observation.timestamp,
                last_seen=observation.timestamp,
            )
            return [
                self._open[observation.bucket_id].as_span(
                    observation.timestamp
                )
            ]

        if observation.timestamp < current.last_seen:
            raise ValueError(
                "Activity observations must be chronological per bucket."
            )

        gap = (observation.timestamp - current.last_seen).total_seconds()
        same_data = observation.content_hash == current.observation.content_hash

        if same_data and gap <= self.pulsetime_seconds:
            current.last_seen = observation.timestamp
            return [current.as_span(observation.timestamp)]

        maximum_end = current.last_seen + timedelta(
            seconds=self.pulsetime_seconds
        )
        closed_end = min(observation.timestamp, maximum_end)
        closed = current.as_span(closed_end)
        replacement = _OpenHeartbeat(
            observation=observation,
            start=observation.timestamp,
            last_seen=observation.timestamp,
        )
        self._open[observation.bucket_id] = replacement
        return [closed, replacement.as_span(observation.timestamp)]

    def flush(self, end_time: datetime | None = None) -> list[ActivitySpan]:
        spans: list[ActivitySpan] = []
        for heartbeat in self._open.values():
            maximum_end = heartbeat.last_seen + timedelta(
                seconds=self.pulsetime_seconds
            )
            requested_end = end_time or heartbeat.last_seen
            spans.append(heartbeat.as_span(min(requested_end, maximum_end)))
        self._open.clear()
        return spans
