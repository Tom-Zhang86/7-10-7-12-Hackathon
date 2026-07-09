from datetime import datetime, timedelta, timezone
import unittest

from application.summary import DailyDataAggregator


class FakeAPI:
    def __init__(self, timeline) -> None:
        self.timeline = timeline
        self.stats = {
            "total_work_seconds": 3600,
            "session_count": 1,
            "break_count": 1,
            "longest_focus_seconds": 1800,
        }

    def get_today_stats(self):
        return self.stats

    def get_today_timeline(self):
        return self.timeline


class DailyDataAggregatorTest(unittest.TestCase):
    def test_builds_compact_activity_blocks_and_app_totals(self) -> None:
        start = datetime(2026, 7, 9, 9, 0, tzinfo=timezone.utc)
        timeline = [
            {
                "type": "session",
                "timestamp": start,
                "start_time": start,
                "end_time": None,
                "session_id": 1,
                "duration_seconds": 0,
                "break_count": 0,
            },
            self._context(start + timedelta(seconds=10), "Code", "main.py"),
            self._context(start + timedelta(seconds=70), "Code", "main.py"),
            self._context(start + timedelta(seconds=130), "Safari", "Docs"),
            {
                "type": "break",
                "timestamp": start + timedelta(seconds=190),
                "start_time": start + timedelta(seconds=190),
                "end_time": start + timedelta(seconds=250),
                "session_id": 1,
                "break_id": 2,
                "duration_seconds": 60,
            },
        ]
        result = DailyDataAggregator(
            FakeAPI(timeline),
            max_sample_gap_seconds=120,
        ).build_today()

        self.assertEqual(result["stats"]["total_work_seconds"], 3600)
        self.assertEqual(result["context_event_count"], 3)
        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual(len(result["breaks"]), 1)
        self.assertEqual(len(result["activity_blocks"]), 2)
        self.assertEqual(
            result["activity_blocks"][0]["estimated_seconds"],
            120,
        )
        self.assertEqual(
            result["frequent_apps"],
            [
                {"app": "Code", "estimated_seconds": 120},
                {"app": "Safari", "estimated_seconds": 0},
            ],
        )
        self.assertIsNone(result["sessions"][0]["end_time"])
        self.assertIsInstance(result["sessions"][0]["start_time"], str)

    def test_handles_empty_context(self) -> None:
        result = DailyDataAggregator(FakeAPI([])).build_today()

        self.assertEqual(result["activity_blocks"], [])
        self.assertEqual(result["frequent_apps"], [])
        self.assertEqual(result["context_event_count"], 0)

    @staticmethod
    def _context(timestamp, app, title):
        return {
            "type": "context_event",
            "timestamp": timestamp,
            "session_id": 1,
            "source": "macos_active_window",
            "payload": {"app": app, "window_title": title},
        }


if __name__ == "__main__":
    unittest.main()
