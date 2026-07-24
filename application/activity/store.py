from datetime import datetime, timedelta
import json
from typing import Any

from application.activity.models import ActivitySegment, ActivitySpan
from database.connection import Database
from utils.time_utils import parse_datetime, utc_now


class ActivityStore:
    """Persistence for ActivityWatch-inspired internal buckets and events."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def ensure_bucket(
        self,
        bucket_id: str,
        event_type: str,
        source: str,
        device_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now().isoformat()
        metadata_json = json.dumps(metadata or {}, sort_keys=True)
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO activity_buckets (
                    id,
                    event_type,
                    source,
                    device_id,
                    metadata_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    event_type = excluded.event_type,
                    source = excluded.source,
                    device_id = CASE
                        WHEN excluded.device_id != '' THEN excluded.device_id
                        ELSE activity_buckets.device_id
                    END,
                    metadata_json = CASE
                        WHEN excluded.metadata_json != '{}' THEN excluded.metadata_json
                        ELSE activity_buckets.metadata_json
                    END,
                    updated_at = excluded.updated_at
                """,
                (
                    bucket_id,
                    event_type,
                    source,
                    device_id,
                    metadata_json,
                    now,
                    now,
                ),
            )

    def upsert_span(self, span: ActivitySpan) -> int:
        self.ensure_bucket(
            span.bucket_id,
            span.event_type,
            span.source,
        )
        now = utc_now().isoformat()
        data_json = json.dumps(span.data, sort_keys=True)
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO activity_events (
                    bucket_id,
                    start_time,
                    duration_seconds,
                    data_json,
                    content_hash,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bucket_id, start_time, content_hash) DO UPDATE SET
                    duration_seconds = excluded.duration_seconds,
                    data_json = excluded.data_json,
                    updated_at = excluded.updated_at
                """,
                (
                    span.bucket_id,
                    span.start.isoformat(),
                    span.duration_seconds,
                    data_json,
                    span.content_hash,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT id FROM activity_events
                WHERE bucket_id = ? AND start_time = ? AND content_hash = ?
                """,
                (
                    span.bucket_id,
                    span.start.isoformat(),
                    span.content_hash,
                ),
            ).fetchone()
        return int(row["id"])

    def list_spans(
        self,
        start: datetime,
        end: datetime,
        bucket_id: str | None = None,
    ) -> list[ActivitySpan]:
        parameters: list[Any] = [end.isoformat(), start.isoformat()]
        bucket_filter = ""
        if bucket_id is not None:
            bucket_filter = " AND event.bucket_id = ?"
            parameters.append(bucket_id)

        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT event.*, bucket.event_type, bucket.source
                FROM activity_events AS event
                JOIN activity_buckets AS bucket ON bucket.id = event.bucket_id
                WHERE event.start_time < ?
                  AND julianday(event.start_time)
                      + (event.duration_seconds / 86400.0) >= julianday(?)
                  {bucket_filter}
                ORDER BY event.start_time ASC
                """,
                parameters,
            ).fetchall()

        spans: list[ActivitySpan] = []
        for row in rows:
            span_start = parse_datetime(row["start_time"])
            span_end = span_start + timedelta(
                seconds=float(row["duration_seconds"])
            )
            span = ActivitySpan(
                start=span_start,
                end=span_end,
                bucket_id=str(row["bucket_id"]),
                event_type=str(row["event_type"]),
                source=str(row["source"]),
                data=json.loads(row["data_json"]),
                content_hash=str(row["content_hash"]),
                event_id=int(row["id"]),
            )
            if span.end >= start:
                spans.append(span)
        return spans

    def upsert_segment(self, segment: ActivitySegment) -> int:
        if not segment.segment_hash:
            raise ValueError("Activity segment_hash must be non-empty.")
        now = utc_now().isoformat()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO activity_segments (
                    segment_hash,
                    session_id,
                    start_time,
                    end_time,
                    presence_state,
                    activity_type,
                    category,
                    confidence,
                    evidence_json,
                    source_event_ids_json,
                    classifier_version,
                    user_corrected,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(segment_hash) DO UPDATE SET
                    session_id = excluded.session_id,
                    start_time = excluded.start_time,
                    end_time = excluded.end_time,
                    presence_state = excluded.presence_state,
                    activity_type = excluded.activity_type,
                    category = CASE
                        WHEN activity_segments.user_corrected = 1
                        THEN activity_segments.category
                        ELSE excluded.category
                    END,
                    confidence = CASE
                        WHEN activity_segments.user_corrected = 1
                        THEN activity_segments.confidence
                        ELSE excluded.confidence
                    END,
                    evidence_json = excluded.evidence_json,
                    source_event_ids_json = excluded.source_event_ids_json,
                    classifier_version = CASE
                        WHEN activity_segments.user_corrected = 1
                        THEN activity_segments.classifier_version
                        ELSE excluded.classifier_version
                    END,
                    updated_at = excluded.updated_at
                """,
                (
                    segment.segment_hash,
                    segment.session_id,
                    segment.start.isoformat(),
                    segment.end.isoformat(),
                    segment.presence_state,
                    segment.activity_type,
                    segment.category,
                    segment.confidence,
                    json.dumps(segment.evidence, sort_keys=True),
                    json.dumps(segment.source_event_ids),
                    segment.classifier_version,
                    int(segment.user_corrected),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT id FROM activity_segments WHERE segment_hash = ?",
                (segment.segment_hash,),
            ).fetchone()
        return int(row["id"])

    def list_segments(
        self,
        start: datetime,
        end: datetime,
    ) -> list[ActivitySegment]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM activity_segments
                WHERE start_time < ? AND end_time > ?
                ORDER BY start_time ASC
                """,
                (end.isoformat(), start.isoformat()),
            ).fetchall()
        return [self._row_to_segment(row) for row in rows]

    def get_segment(self, segment_hash: str) -> ActivitySegment | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM activity_segments WHERE segment_hash = ?",
                (segment_hash,),
            ).fetchone()
        return self._row_to_segment(row) if row else None

    def delete_stale_segments(
        self,
        start: datetime,
        end: datetime,
        valid_hashes: set[str],
    ) -> None:
        parameters: list[Any] = [end.isoformat(), start.isoformat()]
        keep_clause = ""
        if valid_hashes:
            placeholders = ",".join("?" for _ in valid_hashes)
            keep_clause = f" AND segment_hash NOT IN ({placeholders})"
            parameters.extend(sorted(valid_hashes))
        with self.database.connect() as connection:
            connection.execute(
                f"""
                DELETE FROM activity_segments
                WHERE start_time < ?
                  AND end_time > ?
                  AND user_corrected = 0
                  {keep_clause}
                """,
                parameters,
            )

    @staticmethod
    def _row_to_segment(row) -> ActivitySegment:
        return ActivitySegment(
            session_id=int(row["session_id"])
            if row["session_id"] is not None
            else None,
            start=parse_datetime(row["start_time"]),
            end=parse_datetime(row["end_time"]),
            presence_state=str(row["presence_state"]),
            activity_type=str(row["activity_type"]),
            category=str(row["category"]),
            confidence=float(row["confidence"]),
            evidence=json.loads(row["evidence_json"]),
            source_event_ids=tuple(json.loads(row["source_event_ids_json"])),
            classifier_version=str(row["classifier_version"]),
            user_corrected=bool(row["user_corrected"]),
            segment_hash=str(row["segment_hash"]),
        )
