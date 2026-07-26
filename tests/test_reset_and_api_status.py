from pathlib import Path
import tempfile
import unittest

from application.providers import ProviderSelection
from application.summary.store import SummaryStore
from application.ui.dashboard import probe_api_connection
from models.state import PresenceState
from services.ai_desk_api import AIDeskPresenceAPI


class FakeProviderSettings:
    def __init__(self, api_key: str | None) -> None:
        self.api_key = api_key

    def load(self):
        return ProviderSelection("openai", "gpt-5.4-mini")

    def get_api_key(self, _provider_id):
        return self.api_key


class FakeConfigurableClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = []

    def validate(self, provider_id, api_key, timeout_seconds):
        self.calls.append((provider_id, api_key, timeout_seconds))
        if self.error:
            raise self.error


class APIConnectionStatusTest(unittest.TestCase):
    def test_reports_connected_only_after_validation(self) -> None:
        client = FakeConfigurableClient()

        status = probe_api_connection(
            FakeProviderSettings("saved-key"),
            client,
        )

        self.assertEqual(status.state, "connected")
        self.assertEqual(client.calls, [("openai", "saved-key", 8.0)])

    def test_reports_missing_key_and_validation_failure(self) -> None:
        disconnected = probe_api_connection(
            FakeProviderSettings(None),
            FakeConfigurableClient(),
        )
        failed = probe_api_connection(
            FakeProviderSettings("bad-key"),
            FakeConfigurableClient(RuntimeError("unauthorized")),
        )

        self.assertEqual(disconnected.state, "disconnected")
        self.assertEqual(failed.state, "error")


class ClearAllDataTest(unittest.TestCase):
    def test_clears_activity_logs_and_resets_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            api = AIDeskPresenceAPI(
                db_path=root / "api.db",
                log_dir=root / "logs",
            )
            session = api.start_work()
            api.record_context_event(
                session_id=session.id,
                source="macos_active_window",
                payload={"app": "Code"},
            )
            api.start_break()
            log_path = root / "logs" / "manual.log"
            log_path.write_text("sensitive event", encoding="utf-8")

            api.clear_all_data()

            self.assertEqual(api.get_current_state(), PresenceState.IDLE)
            self.assertIsNone(api.get_active_session())
            self.assertEqual(api.get_today_timeline(), [])
            self.assertEqual(
                api.get_today_stats(),
                {
                    "total_work_seconds": 0,
                    "session_count": 0,
                    "break_count": 0,
                    "longest_focus_seconds": 0,
                },
            )
            self.assertFalse(log_path.exists())
            self.assertEqual(api.start_work().id, 1)
            api.close()

    def test_summary_store_clear_preserves_unrelated_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = SummaryStore(root)
            (root / "2026-07-24.json").write_text("{}", encoding="utf-8")
            retained = root / "notes.txt"
            retained.write_text("keep", encoding="utf-8")

            store.clear()

            self.assertFalse((root / "2026-07-24.json").exists())
            self.assertTrue(retained.exists())


if __name__ == "__main__":
    unittest.main()
