import unittest
from datetime import datetime, timezone

from application.ui.dashboard import load_dashboard_snapshot
from models.state import PresenceState


class FakeDashboardAPI:
    def __init__(self) -> None:
        self.timeline_calls = 0

    def get_current_state(self):
        return PresenceState.WORKING

    def get_today_stats(self):
        return {
            "total_work_seconds": 120,
            "longest_focus_seconds": 90,
            "break_count": 1,
        }

    def get_today_timeline(self):
        self.timeline_calls += 1
        now = datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc)
        return [
            {
                "type": "session",
                "timestamp": now,
                "start_time": now,
                "end_time": None,
                "session_id": 1,
                "duration_seconds": 0,
            }
        ]


class DashboardRefreshTest(unittest.TestCase):
    def test_skips_timeline_query_for_regular_stats_refresh(self) -> None:
        api = FakeDashboardAPI()

        snapshot = load_dashboard_snapshot(api, include_timeline=False)

        self.assertEqual(snapshot.state, PresenceState.WORKING)
        self.assertEqual(snapshot.stats["total_work_seconds"], 120)
        self.assertIsNone(snapshot.timeline_rows)
        self.assertEqual(api.timeline_calls, 0)

    def test_builds_timeline_rows_when_requested(self) -> None:
        api = FakeDashboardAPI()

        snapshot = load_dashboard_snapshot(api, include_timeline=True)

        self.assertEqual(api.timeline_calls, 1)
        self.assertEqual(len(snapshot.timeline_rows), 1)
        self.assertEqual(snapshot.timeline_rows[0].detail, "工作开始")


if __name__ == "__main__":
    unittest.main()
