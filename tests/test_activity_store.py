from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from application.activity import ActivityObservation, ActivitySpan, ActivityStore
from database.connection import Database


class ActivityStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp_dir.name) / "activity.db")
        self.database.initialize()
        self.store = ActivityStore(self.database)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_schema_is_additive_and_contains_mvp_activity_tables(self) -> None:
        with self.database.connect() as connection:
            names = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }

        self.assertTrue(
            {
                "sessions",
                "breaks",
                "context_events",
                "activity_buckets",
                "activity_events",
                "activity_segments",
            }.issubset(names)
        )

    def test_upserts_heartbeat_duration_and_lists_overlapping_spans(self) -> None:
        start = datetime(2026, 7, 23, 15, 0, tzinfo=timezone.utc)
        value = ActivityObservation(
            timestamp=start,
            bucket_id="window-host",
            event_type="currentwindow",
            source="macos_active_window",
            data={"app": "Code", "window_title": "main.py"},
        )
        initial = ActivitySpan(
            start=start,
            end=start,
            bucket_id=value.bucket_id,
            event_type=value.event_type,
            source=value.source,
            data=value.data,
            content_hash=value.content_hash,
        )
        updated = ActivitySpan(
            start=start,
            end=start + timedelta(seconds=10),
            bucket_id=value.bucket_id,
            event_type=value.event_type,
            source=value.source,
            data=value.data,
            content_hash=value.content_hash,
        )

        first_id = self.store.upsert_span(initial)
        second_id = self.store.upsert_span(updated)
        spans = self.store.list_spans(
            start - timedelta(seconds=1),
            start + timedelta(seconds=20),
        )

        self.assertEqual(first_id, second_id)
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].duration_seconds, 10)
        self.assertEqual(spans[0].data["app"], "Code")

    def test_initialize_migrates_phase_three_activity_segments(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "phase-three.db"
        connection = sqlite3.connect(legacy_path)
        connection.execute(
            """
            CREATE TABLE activity_segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                presence_state TEXT NOT NULL,
                activity_type TEXT NOT NULL DEFAULT 'unknown',
                category TEXT NOT NULL DEFAULT 'unknown',
                confidence REAL NOT NULL DEFAULT 0,
                evidence_json TEXT NOT NULL DEFAULT '{}',
                source_event_ids_json TEXT NOT NULL DEFAULT '[]',
                classifier_version TEXT NOT NULL DEFAULT 'unclassified',
                user_corrected INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO activity_segments (
                start_time,
                end_time,
                presence_state,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "2026-07-23T15:00:00+00:00",
                "2026-07-23T15:01:00+00:00",
                "working",
                "2026-07-23T15:00:00+00:00",
                "2026-07-23T15:00:00+00:00",
            ),
        )
        connection.commit()
        connection.close()

        Database(legacy_path).initialize()

        connection = sqlite3.connect(legacy_path)
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(activity_segments)"
            ).fetchall()
        }
        segment_hash = connection.execute(
            "SELECT segment_hash FROM activity_segments"
        ).fetchone()[0]
        connection.close()

        self.assertIn("segment_hash", columns)
        self.assertEqual(segment_hash, "legacy-1")


if __name__ == "__main__":
    unittest.main()
