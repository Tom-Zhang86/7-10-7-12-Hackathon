import tempfile
import time
import unittest
from pathlib import Path
from threading import Event

from application.handoff import (
    A2AResult,
    HandoffInput,
    HandoffOrchestrator,
    HandoffStatus,
    HandoffStore,
    TaskCapsule,
)
from application.handoff.a2a_client import A2AHandoffClient
from application.handoff.report import render_markdown, save_report
from events.event_types import PresenceDetected, PresenceLost
from services.ai_desk_api import AIDeskPresenceAPI


def artifact(handoff_id: str) -> dict:
    return {
        "schema_version": "aidesk.research-handoff.result.v1",
        "handoff_id": handoff_id,
        "status": "completed",
        "executive_summary": "A concrete research result.",
        "findings": [
            {
                "claim": "Presence can drive task ownership transfer.",
                "source_ids": ["W1"],
                "confidence": "high",
            }
        ],
        "sources": [
            {
                "id": "W1",
                "title": "Research handoff",
                "url": "https://example.test/source",
                "publisher": "Test",
                "publication_year": 2026,
            }
        ],
        "open_questions": [],
        "recommended_next_actions": ["Review W1"],
        "resume_context": "Continue from finding W1.",
        "limitations": [],
    }


class FakeClient:
    def __init__(self) -> None:
        self.calls = []
        self.called = Event()

    def send_task(self, capsule):
        self.calls.append(capsule)
        self.called.set()
        return A2AResult("task-1", "context-1", "completed", artifact(capsule.handoff_id))


class FakeActions:
    def __init__(self) -> None:
        self.notifications = []
        self.delivered = []
        self.delivery = Event()

    def notify(self, title, message):
        self.notifications.append((title, message))

    def deliver(self, record):
        self.delivered.append(record)
        self.delivery.set()
        return Path("report.md")


class HandoffStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store = HandoffStore(self.root / "handoffs.sqlite3")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_capsule_round_trip_and_atomic_claim(self) -> None:
        capsule = TaskCapsule.create(
            "Compare research agents",
            inputs=[HandoffInput("url", "https://example.test")],
        )
        self.store.create(capsule)
        claimed = self.store.claim_next_armed()
        self.assertEqual(claimed.capsule, capsule)
        self.assertEqual(claimed.status, HandoffStatus.DELEGATING)
        self.assertIsNone(self.store.claim_next_armed())

    def test_result_transitions_to_ready_and_returned(self) -> None:
        capsule = TaskCapsule.create("Research A2A")
        self.store.create(capsule)
        self.store.claim_next_armed()
        self.store.mark_running(capsule.handoff_id)
        ready = self.store.complete(
            capsule.handoff_id,
            a2a_task_id="t1",
            context_id="c1",
            artifact=artifact(capsule.handoff_id),
        )
        self.assertEqual(ready.status, HandoffStatus.READY)
        self.assertEqual(self.store.next_deliverable().capsule.handoff_id, capsule.handoff_id)
        returned = self.store.mark_returned(capsule.handoff_id)
        self.assertEqual(returned.status, HandoffStatus.RETURNED)

    def test_markdown_report_preserves_sources_and_resume_context(self) -> None:
        capsule = TaskCapsule.create("Research A2A")
        self.store.create(capsule)
        self.store.claim_next_armed()
        self.store.mark_running(capsule.handoff_id)
        record = self.store.complete(
            capsule.handoff_id,
            a2a_task_id="t1",
            context_id="c1",
            artifact=artifact(capsule.handoff_id),
        )
        text = render_markdown(record)
        self.assertIn("https://example.test/source", text)
        self.assertIn("Continue from finding W1", text)
        path = save_report(record, self.root / "reports")
        self.assertTrue(path.exists())
        self.assertTrue(path.with_name("artifact.json").exists())


class HandoffOrchestratorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.api = AIDeskPresenceAPI(
            db_path=self.root / "presence.db",
            log_dir=self.root / "logs",
        )
        self.store = HandoffStore(self.root / "handoffs.sqlite3")
        self.client = FakeClient()
        self.actions = FakeActions()
        self.api.start()
        self.api.post_event(PresenceDetected())
        self.api.wait_until_idle()

    def tearDown(self) -> None:
        if hasattr(self, "orchestrator"):
            self.orchestrator.stop()
        self.api.close()
        self.temp_dir.cleanup()

    def test_absence_delegates_once_and_return_delivers(self) -> None:
        capsule = TaskCapsule.create("Research presence handoff")
        self.store.create(capsule)
        self.orchestrator = HandoffOrchestrator(
            self.api,
            self.store,
            self.client,
            self.actions,
            grace_seconds=0.01,
        )
        self.orchestrator.start()

        self.api.post_event(PresenceLost())
        self.api.wait_until_idle()
        self.assertTrue(self.client.called.wait(1))

        self.api.post_event(PresenceDetected())
        self.api.wait_until_idle()
        self.assertTrue(self.actions.delivery.wait(1))
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(len(self.actions.delivered), 1)
        self.assertEqual(
            self.store.get(capsule.handoff_id).status,
            HandoffStatus.RETURNED,
        )

    def test_return_inside_grace_period_cancels_delegation(self) -> None:
        capsule = TaskCapsule.create("Do not delegate a false absence")
        self.store.create(capsule)
        self.orchestrator = HandoffOrchestrator(
            self.api,
            self.store,
            self.client,
            self.actions,
            grace_seconds=0.2,
        )
        self.orchestrator.start()

        self.api.post_event(PresenceLost())
        self.api.wait_until_idle()
        self.api.post_event(PresenceDetected())
        self.api.wait_until_idle()
        time.sleep(0.3)
        self.assertEqual(self.client.calls, [])
        self.assertEqual(
            self.store.get(capsule.handoff_id).status,
            HandoffStatus.ARMED,
        )

    def test_bad_agent_url_is_rejected_before_network(self) -> None:
        with self.assertRaises(ValueError):
            A2AHandoffClient("file:///tmp/agent")


if __name__ == "__main__":
    unittest.main()
