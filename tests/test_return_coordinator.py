import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from application.owner_handoff.domain.manifest import DuplicationManifest, FileRecord
from application.owner_handoff.domain.resume import ResumeReport, SafetyFailureRecord
from application.owner_handoff.return_coordinator import (
    ReturnCoordinator,
    StepDidNotFinishInGracePeriodError,
)
from application.owner_handoff.state_machine import OwnerHandoffState
from application.owner_handoff.store import OwnerHandoffStore

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
S = OwnerHandoffState


class ReturnCoordinatorTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.boundary = Path(self.temp_dir.name)
        self.store = OwnerHandoffStore(self.boundary / "db.sqlite3")
        self.store.create_task("task-1")
        self._drive_to_executing()

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def _drive_to_executing(self) -> None:
        self.store.apply_transition(
            task_id="task-1", event_id="e1", from_state=S.OBSERVING,
            to_state=S.LEFT_CANDIDATE, reason="r1",
        )
        self.store.apply_transition(
            task_id="task-1", event_id="e2", from_state=S.LEFT_CANDIDATE,
            to_state=S.OWNER_LEFT_CONFIRMED, reason="r2",
        )
        self.store.apply_transition(
            task_id="task-1", event_id="e3", from_state=S.OWNER_LEFT_CONFIRMED,
            to_state=S.QUESTION_PENDING, reason="r3",
        )
        self.store.apply_transition(
            task_id="task-1", event_id="e4", from_state=S.QUESTION_PENDING,
            to_state=S.AUTHORIZED, reason="r4",
        )
        self.store.apply_transition(
            task_id="task-1", event_id="e5", from_state=S.AUTHORIZED,
            to_state=S.EXECUTING, reason="research skill: no duplication",
        )

    def _manifest(self, *, source: Path, duplicate: Path) -> DuplicationManifest:
        return DuplicationManifest(
            session_id="s1", task_id="task-1", source_path=str(source),
            duplicate_path=str(duplicate), copied_relative_paths=(), excluded=(),
            original_files=(FileRecord("main.py", "a" * 64, 1),), post_copy_hashes=(),
            created_at=NOW,
        )


class ReturnRequestTest(ReturnCoordinatorTestBase):
    def test_request_return_transitions_and_marks_requested(self) -> None:
        coordinator = ReturnCoordinator(store=self.store)
        self.assertFalse(coordinator.is_return_requested("task-1"))
        coordinator.request_return(task_id="task-1")
        self.assertTrue(coordinator.is_return_requested("task-1"))
        self.assertEqual(self.store.get_task("task-1").state, S.RETURN_REQUESTED)


class RunCurrentStepTest(ReturnCoordinatorTestBase):
    def test_step_completes_normally_when_no_return_requested(self) -> None:
        coordinator = ReturnCoordinator(store=self.store)
        result = coordinator.run_current_step(lambda: "done", task_id="task-1")
        self.assertEqual(result, "done")

    def test_step_allowed_to_finish_within_grace_period_after_return(self) -> None:
        coordinator = ReturnCoordinator(store=self.store, return_grace_period_seconds=2.0)
        coordinator.request_return(task_id="task-1")
        release = threading.Event()

        def step():
            release.wait(timeout=1.0)
            return "finished-within-grace"

        # Release the step almost immediately from a watcher thread so the
        # test stays fast without a real sleep in the assertion path.
        threading.Timer(0.01, release.set).start()
        result = coordinator.run_current_step(step, task_id="task-1")
        self.assertEqual(result, "finished-within-grace")

    def test_step_terminated_and_error_raised_if_grace_period_exceeded(self) -> None:
        coordinator = ReturnCoordinator(store=self.store, return_grace_period_seconds=0.05)
        coordinator.request_return(task_id="task-1")
        release = threading.Event()
        terminated = []

        def step():
            release.wait()  # blocks until terminate_fn sets it
            return "should-not-be-reached-in-time"

        def terminate_fn():
            terminated.append(True)
            release.set()

        with self.assertRaises(StepDidNotFinishInGracePeriodError):
            coordinator.run_current_step(step, task_id="task-1", terminate_fn=terminate_fn)
        self.assertEqual(terminated, [True])

    def test_non_cooperative_step_bounded_by_grace_period_not_by_step_duration(self) -> None:
        """Repair timing requirement: return grace = 0.02s, a
        non-cooperative fake step lasts 0.25s (and never checks any stop
        signal at all) -- the coordinator call must return/fail within a
        small bounded tolerance, NOT after the full 0.25s. Return arrives
        AFTER the step has actually started running (a background timer
        fires request_return once the step is underway), not before
        run_current_step is called."""

        import time

        coordinator = ReturnCoordinator(store=self.store, return_grace_period_seconds=0.02)
        step_started = threading.Event()
        step_finished_naturally = threading.Event()

        def non_cooperative_step():
            step_started.set()
            time.sleep(0.25)  # never checks any stop signal
            step_finished_naturally.set()
            return "finished-late"

        # Fires only once the step is confirmed to already be running.
        def _request_return_once_started():
            step_started.wait(timeout=1.0)
            coordinator.request_return(task_id="task-1")

        threading.Thread(target=_request_return_once_started, daemon=True).start()

        start = time.monotonic()
        with self.assertRaises(StepDidNotFinishInGracePeriodError):
            coordinator.run_current_step(non_cooperative_step, task_id="task-1")
        elapsed = time.monotonic() - start

        # Bounded tolerance: well under the step's 0.25s duration.
        self.assertLess(elapsed, 0.2)
        self.assertFalse(step_finished_naturally.is_set())

        # No subsequent step may start once return has been requested.
        started_second_step = []

        def maybe_start_another():
            if coordinator.is_return_requested("task-1"):
                return
            started_second_step.append(True)
            coordinator.run_current_step(lambda: "second", task_id="task-1")

        maybe_start_another()
        self.assertEqual(started_second_step, [])

    def test_does_not_start_another_step_after_return_requested(self) -> None:
        """The orchestrator-level contract: once return is requested, the
        caller must consult is_return_requested() before starting a new
        step at all. This test documents/asserts that contract directly."""

        coordinator = ReturnCoordinator(store=self.store)
        coordinator.request_return(task_id="task-1")
        calls = []

        def maybe_start_step():
            if coordinator.is_return_requested("task-1"):
                return None
            calls.append(1)
            return coordinator.run_current_step(lambda: "ran", task_id="task-1")

        result = maybe_start_step()
        self.assertIsNone(result)
        self.assertEqual(calls, [])


class FinalizeTest(ReturnCoordinatorTestBase):
    def test_finalize_with_unchanged_original_reports_clean(self) -> None:
        with tempfile.TemporaryDirectory() as src_tmp, tempfile.TemporaryDirectory() as dup_tmp:
            source = Path(src_tmp)
            duplicate = Path(dup_tmp)
            (source / "main.py").write_text("print(1)\n")
            import hashlib

            digest = hashlib.sha256((source / "main.py").read_bytes()).hexdigest()
            manifest = DuplicationManifest(
                session_id="s1", task_id="task-1", source_path=str(source),
                duplicate_path=str(duplicate), copied_relative_paths=("main.py",), excluded=(),
                original_files=(FileRecord("main.py", digest, 8),), post_copy_hashes=(),
                created_at=NOW,
            )
            coordinator = ReturnCoordinator(store=self.store)
            coordinator.request_return(task_id="task-1")
            report, verified = coordinator.finalize(
                task_id="task-1", manifest=manifest, selected_task="Fix the test",
                work_completed=("did the thing",),
            )
            self.assertEqual(report.status, "ready_for_review")
            self.assertEqual(report.original_files_modified, ())
            self.assertEqual(self.store.get_task("task-1").state, S.READY_FOR_REVIEW)

    def test_finalize_with_tampered_original_fails_closed_never_ready_for_review(self) -> None:
        """Repair: a verification failure must transition to FAILED (never
        READY_FOR_REVIEW) and produce a SafetyFailureRecord (never a
        ResumeReport, which cannot even be constructed with a non-empty
        original_files_modified)."""

        with tempfile.TemporaryDirectory() as src_tmp, tempfile.TemporaryDirectory() as dup_tmp:
            source = Path(src_tmp)
            duplicate = Path(dup_tmp)
            (source / "main.py").write_text("print(1)\n")
            manifest = DuplicationManifest(
                session_id="s1", task_id="task-1", source_path=str(source),
                duplicate_path=str(duplicate), copied_relative_paths=("main.py",), excluded=(),
                original_files=(FileRecord("main.py", "0" * 64, 8),), post_copy_hashes=(),
                created_at=NOW,
            )
            coordinator = ReturnCoordinator(store=self.store)
            coordinator.request_return(task_id="task-1")
            result, verified = coordinator.finalize(
                task_id="task-1", manifest=manifest, selected_task="Fix the test",
            )
            self.assertIsInstance(result, SafetyFailureRecord)
            self.assertNotIsInstance(result, ResumeReport)
            self.assertEqual(result.status, "failed_verification")
            self.assertIn("main.py", result.original_files_modified)
            self.assertEqual(self.store.get_task("task-1").state, S.FAILED)

    def test_deliver_control_transitions_to_returned(self) -> None:
        with tempfile.TemporaryDirectory() as src_tmp, tempfile.TemporaryDirectory() as dup_tmp:
            source = Path(src_tmp)
            (source / "main.py").write_text("print(1)\n")
            import hashlib

            digest = hashlib.sha256((source / "main.py").read_bytes()).hexdigest()
            manifest = DuplicationManifest(
                session_id="s1", task_id="task-1", source_path=str(source),
                duplicate_path=dup_tmp, copied_relative_paths=("main.py",), excluded=(),
                original_files=(FileRecord("main.py", digest, 8),), post_copy_hashes=(),
                created_at=NOW,
            )
            coordinator = ReturnCoordinator(store=self.store)
            coordinator.request_return(task_id="task-1")
            coordinator.finalize(task_id="task-1", manifest=manifest, selected_task="x")
            coordinator.deliver_control(task_id="task-1")
            self.assertEqual(self.store.get_task("task-1").state, S.RETURNED)
            self.assertFalse(coordinator.is_return_requested("task-1"))


if __name__ == "__main__":
    unittest.main()
