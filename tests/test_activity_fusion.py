from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from application.activity import (
    ActivityFusionService,
    ActivityObservation,
    ActivitySpan,
    ActivityStore,
)
from database.connection import Database


DAY = date(2026, 7, 23)
START = datetime(2026, 7, 23, 15, 0, tzinfo=timezone.utc)


class TimelineAPI:
    def get_timeline_for_day(self, _target_date):
        return [
            {
                "type": "session",
                "timestamp": START,
                "start_time": START,
                "end_time": START + timedelta(seconds=60),
                "session_id": 3,
                "duration_seconds": 50,
                "break_count": 1,
            },
            {
                "type": "break",
                "timestamp": START + timedelta(seconds=30),
                "start_time": START + timedelta(seconds=30),
                "end_time": START + timedelta(seconds=40),
                "session_id": 3,
                "break_id": 1,
                "duration_seconds": 10,
            },
        ]


def make_span(
    bucket: str,
    event_type: str,
    source: str,
    offset: int,
    duration: int,
    data: dict,
) -> ActivitySpan:
    observation = ActivityObservation(
        timestamp=START + timedelta(seconds=offset),
        bucket_id=bucket,
        event_type=event_type,
        source=source,
        data=data,
    )
    return ActivitySpan(
        start=observation.timestamp,
        end=observation.timestamp + timedelta(seconds=duration),
        bucket_id=bucket,
        event_type=event_type,
        source=source,
        data=data,
        content_hash=observation.content_hash,
    )


class ActivityFusionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "fusion.db")
        self.database.initialize()
        self.store = ActivityStore(self.database)
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions (
                    id, start_time, end_time, duration_seconds,
                    break_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    3,
                    START.isoformat(),
                    (START + timedelta(seconds=60)).isoformat(),
                    50,
                    1,
                    START.isoformat(),
                    START.isoformat(),
                ),
            )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_intersects_presence_and_prefers_browser_semantics(self) -> None:
        self.store.upsert_span(
            make_span(
                "window",
                "currentwindow",
                "macos_active_window",
                0,
                60,
                {"app": "Google Chrome", "window_title": "Course"},
            )
        )
        self.store.upsert_span(
            make_span(
                "browser",
                "web.semantic",
                "chrome_semantic",
                10,
                40,
                {
                    "url": "https://youtube.com/watch",
                    "page_title": "Linear Algebra Lecture",
                    "media": {"playing": True},
                },
            )
        )
        service = ActivityFusionService(
            TimelineAPI(),
            self.store,
            clock=lambda: START + timedelta(seconds=60),
        )

        segments = service.build_for_day(DAY)

        self.assertEqual(len(segments), 5)
        browser = [
            segment
            for segment in segments
            if segment.evidence.get("url")
        ]
        self.assertEqual(len(browser), 3)
        self.assertTrue(
            all(segment.evidence["app"] == "Google Chrome" for segment in browser)
        )
        away = next(
            segment for segment in segments if segment.presence_state == "away"
        )
        self.assertEqual(away.category, "background_playback")
        self.assertEqual(away.duration_seconds, 10)
        self.assertEqual(len(self.store.list_segments(START, START + timedelta(minutes=1))), 5)
