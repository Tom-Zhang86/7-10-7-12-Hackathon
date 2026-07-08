import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from database.connection import Database
from database.repository import SessionRepository
from models.state import PresenceState
from services.stats_service import StatsService
from session.manager import SessionManager


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs) -> None:
        self.current += timedelta(**kwargs)


class SessionFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "test.db"
        self.database = Database(db_path)
        self.database.initialize()
        self.repository = SessionRepository(self.database)
        self.clock = FakeClock(
            datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc)
        )
        self.manager = SessionManager(self.repository, self.clock)
        self.stats = StatsService(self.repository, self.clock)

    def tearDown(self) -> None:
        self.database.close()
        self.temp_dir.cleanup()

    def test_presence_flow_records_session_breaks_and_stats(self) -> None:
        self.assertEqual(self.manager.state, PresenceState.IDLE)

        self.manager.handle_presence(True)
        self.clock.advance(minutes=50)
        self.manager.handle_presence(False)
        self.clock.advance(minutes=10)
        self.manager.handle_presence(True)
        self.clock.advance(minutes=40)
        finished = self.manager.finish_day()

        self.assertIsNotNone(finished)
        self.assertEqual(self.manager.state, PresenceState.FINISHED)
        self.assertEqual(finished.duration_seconds, 90 * 60)
        self.assertEqual(finished.break_count, 1)

        daily_stats = self.stats.get_daily_stats(self.clock.current.date())
        self.assertEqual(daily_stats.total_work_seconds, 90 * 60)
        self.assertEqual(daily_stats.session_count, 1)
        self.assertEqual(daily_stats.break_count, 1)
        self.assertEqual(daily_stats.longest_focus_seconds, 50 * 60)

    def test_repeated_same_presence_event_is_idempotent(self) -> None:
        self.manager.handle_presence(True)
        self.manager.handle_presence(True)

        sessions = self.repository.list_sessions_for_day(
            self.clock.current.date()
        )

        self.assertEqual(len(sessions), 1)
        self.assertEqual(self.manager.state, PresenceState.WORKING)


if __name__ == "__main__":
    unittest.main()
