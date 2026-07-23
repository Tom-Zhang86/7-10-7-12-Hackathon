import tempfile
import unittest
from pathlib import Path

from services.ai_desk_api import AIDeskPresenceAPI
from models.state import PresenceState


class PublicAPITest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.api = AIDeskPresenceAPI(
            db_path=self.root / "api.db",
            log_dir=self.root / "logs",
        )

    def tearDown(self) -> None:
        self.api.close()
        self.temp_dir.cleanup()

    def test_record_context_event_and_list_for_day(self) -> None:
        event = self.api.record_context_event(
            session_id=None,
            source="macos_active_window",
            payload={"app": "Terminal", "title": "AI Desk"},
        )

        self.assertIsInstance(event["id"], int)
        self.assertIsNone(event["session_id"])
        self.assertEqual(event["source"], "macos_active_window")
        self.assertEqual(
            event["payload"],
            {"app": "Terminal", "title": "AI Desk"},
        )
        self.assertEqual(
            event["payload_json"],
            '{"app": "Terminal", "title": "AI Desk"}',
        )

        events = self.api.get_context_events_for_day(
            date=event["timestamp"].date()
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["id"], event["id"])

    def test_get_today_timeline_orders_sessions_breaks_and_context(self) -> None:
        session = self.api.start_work()
        context_event = self.api.record_context_event(
            session_id=session.id,
            source="macos_active_window",
            payload={"app": "Editor"},
        )
        break_record = self.api.start_break()

        timeline = self.api.get_today_timeline()
        timeline_types = [item["type"] for item in timeline]

        self.assertEqual(timeline_types, ["session", "context_event", "break"])
        self.assertEqual(timeline[0]["session_id"], session.id)
        self.assertEqual(timeline[1]["session_id"], session.id)
        self.assertEqual(timeline[1]["payload"], {"app": "Editor"})
        self.assertEqual(timeline[1]["source"], "macos_active_window")
        self.assertEqual(timeline[2]["break_id"], break_record.id)
        self.assertEqual(timeline, sorted(timeline, key=lambda item: item["timestamp"]))
        self.assertEqual(context_event["session_id"], session.id)
        self.assertEqual(
            self.api.get_timeline_for_day(context_event["timestamp"].date()),
            timeline,
        )

    def test_presence_ingestion_contract_remains_boolean_to_state(self) -> None:
        self.assertEqual(self.api.ingest_presence(True), PresenceState.WORKING)
        self.assertEqual(self.api.ingest_presence(False), PresenceState.BREAK)
        self.assertEqual(self.api.ingest_presence(True), PresenceState.WORKING)


if __name__ == "__main__":
    unittest.main()
