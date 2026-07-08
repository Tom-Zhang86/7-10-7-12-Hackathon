import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from database.connection import Database
from database.repository import SessionRepository
from events.event_types import Event, PresenceDetected, PresenceLost
from listeners.event_log_listener import EventLogListener
from models.state import PresenceState
from runtime.runtime import Runtime
from services.stats_service import StatsService
from session.manager import SessionManager


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs) -> None:
        self.current += timedelta(**kwargs)


class RuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database = Database(self.root / "runtime.db")
        self.database.initialize()
        self.repository = SessionRepository(self.database)
        self.clock = FakeClock(
            datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc)
        )
        self.manager = SessionManager(self.repository, self.clock)
        self.stats = StatsService(self.repository, self.clock)
        self.runtime = Runtime(self.manager, self.stats, clock=self.clock)
        self.events: list[Event] = []
        self.runtime.subscribe("*", self.events.append)
        self.runtime.subscribe("*", EventLogListener(self.root / "logs"))

    def tearDown(self) -> None:
        if self.runtime.is_running:
            self.runtime.stop()
        self.database.close()
        self.temp_dir.cleanup()

    def test_runtime_processes_events_and_notifies_listeners(self) -> None:
        self.runtime.start()

        self.runtime.post_event(PresenceDetected(timestamp=self.clock.current))
        self.runtime.wait_until_idle()
        self.assertEqual(self.manager.state, PresenceState.WORKING)

        self.clock.advance(minutes=30)
        self.runtime.post_event(PresenceLost(timestamp=self.clock.current))
        self.runtime.wait_until_idle()
        self.assertEqual(self.manager.state, PresenceState.BREAK)

        self.clock.advance(minutes=5)
        self.runtime.post_event(PresenceDetected(timestamp=self.clock.current))
        self.runtime.wait_until_idle()
        self.assertEqual(self.manager.state, PresenceState.WORKING)

        self.clock.advance(minutes=25)
        self.runtime.stop()

        event_names = [event.name for event in self.events]
        self.assertIn("SessionStarted", event_names)
        self.assertIn("BreakStarted", event_names)
        self.assertIn("BreakEnded", event_names)
        self.assertIn("SessionEnded", event_names)
        self.assertIn("StatisticsUpdated", event_names)
        self.assertEqual(self.manager.state, PresenceState.FINISHED)

        stats = self.stats.get_daily_stats(self.clock.current.date())
        self.assertEqual(stats.total_work_seconds, 55 * 60)
        self.assertEqual(stats.break_count, 1)

        log_path = self.root / "logs" / "2026-07-06.log"
        log_text = log_path.read_text(encoding="utf-8")
        self.assertIn("PresenceDetected", log_text)
        self.assertIn("SessionStarted", log_text)
        self.assertIn("SessionEnded", log_text)

    def test_system_event_payload_keys_are_stable(self) -> None:
        self.runtime.start()

        self.runtime.post_event(PresenceDetected(timestamp=self.clock.current))
        self.runtime.wait_until_idle()
        self.clock.advance(minutes=30)
        self.runtime.post_event(PresenceLost(timestamp=self.clock.current))
        self.runtime.wait_until_idle()
        self.clock.advance(minutes=5)
        self.runtime.post_event(PresenceDetected(timestamp=self.clock.current))
        self.runtime.wait_until_idle()
        self.clock.advance(minutes=25)
        self.runtime.stop()

        expected_keys = {
            "StateChanged": {"old_state", "new_state"},
            "SessionStarted": {"session_id", "start_time"},
            "BreakStarted": {"break_id", "session_id", "start_time"},
            "BreakEnded": {
                "break_id",
                "session_id",
                "start_time",
                "end_time",
                "duration_seconds",
            },
            "SessionEnded": {
                "session_id",
                "start_time",
                "end_time",
                "duration_seconds",
                "break_count",
            },
            "StatisticsUpdated": {
                "total_work_seconds",
                "session_count",
                "break_count",
                "longest_focus_seconds",
            },
        }

        for event_name, keys in expected_keys.items():
            matching_events = [
                event for event in self.events if event.name == event_name
            ]
            self.assertTrue(matching_events, event_name)
            for event in matching_events:
                self.assertEqual(set(event.payload), keys)

    def test_listener_failure_does_not_stop_runtime(self) -> None:
        def failing_listener(event: Event) -> None:
            raise RuntimeError(f"Cannot process {event.name}")

        self.runtime.subscribe("PresenceDetected", failing_listener)
        self.runtime.start()

        with self.assertLogs("events.dispatcher", level="ERROR"):
            self.runtime.post_event(PresenceDetected(timestamp=self.clock.current))
            self.runtime.wait_until_idle()

        self.assertTrue(self.runtime.is_running)
        self.assertEqual(self.manager.state, PresenceState.WORKING)
        self.assertIn("SessionStarted", [event.name for event in self.events])


if __name__ == "__main__":
    unittest.main()
