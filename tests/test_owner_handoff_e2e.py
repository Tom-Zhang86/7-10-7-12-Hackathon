import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from application.handoff.models import A2AResult, TaskCapsule
from application.owner_handoff.domain.execution import ExecutionStatus, SkillKind
from application.owner_handoff.domain.presence import (
    RadarState,
    WearableAnswer,
    WearableButton,
    WearableProximityState,
)
from application.owner_handoff.domain.work_context import WorkContext, WorkContextTracker
from application.owner_handoff.execution.coding_executor import CodingTaskRequest, FakeCodingExecutor
from application.owner_handoff.execution.package_installer import PackageInstaller
from application.owner_handoff.execution.permission import PermissionGate, PermissionNotGrantedError
from application.owner_handoff.execution.research_executor import ResearchAgentExecutor
from application.owner_handoff.fusion.leave_detector import LeaveDetector
from application.owner_handoff.fusion.return_detector import ReturnDetector
from application.owner_handoff.adapters.radar import SimulatorRadar
from application.owner_handoff.adapters.wearable import WearableSimulator
from application.owner_handoff.questions.generator import HandoffQuestionGenerator
from application.owner_handoff.questions.lifecycle import QuestionLifecycle
from application.owner_handoff.orchestrator import (
    NoRoutedOptionError,
    OrchestratorBusyError,
    OwnerHandoffOrchestrator,
)
from application.owner_handoff.return_coordinator import ReturnCoordinator
from application.owner_handoff.state_machine import OwnerHandoffState, PermissionKind
from application.owner_handoff.store import OwnerHandoffStore
from application.owner_handoff.workspace.duplicator import verify_original_unchanged

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
DEVICE_ID = "wearable-e2e-001"
S = OwnerHandoffState


class _FakeMonotonic:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeDatetimeClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class _WritingFakeCodingExecutor:
    """A fake coding executor that actually writes to the duplicate, so the
    "accurate files_created/files_modified_in_duplicate" report can be
    tested against real filesystem changes -- never a real subprocess."""

    def __init__(self) -> None:
        self.requests: list[CodingTaskRequest] = []

    @property
    def call_count(self) -> int:
        return len(self.requests)

    def execute(self, request: CodingTaskRequest):
        from application.owner_handoff.domain.execution import ExecutionResult

        self.requests.append(request)
        (request.duplicate_path / "NEW_FILE.txt").write_text("created by fake coding executor\n")
        (request.duplicate_path / "main.py").write_text("print('modified by fake coding')\n")
        return ExecutionResult(status=ExecutionStatus.COMPLETED, summary="fake coding task completed")


class _FakeA2AClient:
    def __init__(self) -> None:
        self.calls: list[TaskCapsule] = []

    def send_task(self, capsule: TaskCapsule) -> A2AResult:
        self.calls.append(capsule)
        return A2AResult(
            task_id="a2a-task-1", context_id="ctx-1", protocol_state="completed",
            artifact={"status": "completed", "executive_summary": "Done.", "handoff_id": capsule.handoff_id},
        )


class _FakeInstallRunner:
    def __init__(self, *, returncode: int = 0) -> None:
        self.calls: list[dict] = []
        self.returncode = returncode

    def run(self, argv, *, cwd):
        from application.owner_handoff.execution.codex_cli import CommandResult

        self.calls.append({"argv": argv, "cwd": cwd})
        return CommandResult(self.returncode, "installed\n")


def _answer(*, question_id: str, button: WearableButton, clock: _FakeDatetimeClock, **overrides) -> WearableAnswer:
    defaults = dict(device_id=DEVICE_ID, timestamp=clock())
    defaults.update(overrides)
    return WearableAnswer(question_id=question_id, button=button, **defaults)


class OwnerHandoffE2ETestBase(unittest.TestCase):
    """Builds one fully-wired orchestrator (fakes only -- no real Codex,
    network, BLE, radar serial, or package install anywhere)."""

    CODING_DIRECTION = "Fix the failing test in the renderer"
    RESEARCH_DIRECTION = "Research the presence fusion approach"

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.boundary = Path(self.temp_dir.name)
        self.session_root = self.boundary / "sessions"
        self.session_root.mkdir()
        self.source = self.boundary / "project"
        self.source.mkdir()
        (self.source / "main.py").write_text("print('original')\n")

        self.store = OwnerHandoffStore(self.boundary / "owner_handoff.sqlite3")
        self.monotonic = _FakeMonotonic()
        self.clock = _FakeDatetimeClock(NOW)

        self.leave_detector = LeaveDetector(
            input_idle_threshold_seconds=5.0,
            owner_leave_confirmation_seconds=10.0,
            monotonic=self.monotonic,
        )
        self.return_detector = ReturnDetector()
        self.radar = SimulatorRadar(clock=self.clock)
        self.wearable = WearableSimulator(DEVICE_ID, clock=self.clock)

        context = WorkContext(
            project="AI Desk", current_task="repair gate", stage="testing",
            possible_next_steps=(self.CODING_DIRECTION, self.RESEARCH_DIRECTION),
            confidence=0.4, updated_at=NOW,
        )
        self.work_context_tracker = WorkContextTracker(clock=self.clock, context=context)
        self.question_generator = HandoffQuestionGenerator(
            question_expiration_seconds=60.0, clock=self.clock,
        )
        self.lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=self.clock)
        self.permission_gate = PermissionGate()
        self.a2a_client = _FakeA2AClient()
        self.research_executor = ResearchAgentExecutor(self.a2a_client)
        self.coding_executor = FakeCodingExecutor()
        self.install_runner = _FakeInstallRunner()
        self.package_installer = PackageInstaller(
            runner=self.install_runner, platform_name="Darwin", clock=self.clock,
        )
        self.return_coordinator = ReturnCoordinator(store=self.store, return_grace_period_seconds=2.0)

        self.orchestrator = self._build_orchestrator()

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def _build_orchestrator(self, *, coding_executor=None, artifact_store=None) -> OwnerHandoffOrchestrator:
        return OwnerHandoffOrchestrator(
            store=self.store,
            source_workspace=self.source,
            allowed_boundary=self.boundary,
            session_root=self.session_root,
            leave_detector=self.leave_detector,
            return_detector=self.return_detector,
            radar=self.radar,
            wearable=self.wearable,
            work_context_tracker=self.work_context_tracker,
            question_generator=self.question_generator,
            lifecycle=self.lifecycle,
            permission_gate=self.permission_gate,
            research_executor=self.research_executor,
            coding_executor=coding_executor if coding_executor is not None else self.coding_executor,
            package_installer=self.package_installer,
            return_coordinator=self.return_coordinator,
            question_expiration_seconds=60.0,
            coding_max_runtime_seconds=300.0,
            coding_max_safe_steps=20,
            clock=self.clock,
            artifact_store=artifact_store,
        )

    def _confirm_leave(self) -> None:
        self.radar.set_state(RadarState.PERSON_ABSENT)
        self.wearable.set_proximity(WearableProximityState.OWNER_AWAY)
        self.orchestrator.evaluate_presence_for_leave()
        self.monotonic.advance(5.0)
        self.orchestrator.evaluate_presence_for_leave()
        self.monotonic.advance(10.0)
        self.orchestrator.evaluate_presence_for_leave()
        self.assertEqual(self.store.get_task(self.orchestrator.task_id).state, S.OWNER_LEFT_CONFIRMED)

    def _ask_and_answer(self, letter: str) -> None:
        question = self.orchestrator.ask_question()
        letter_to_button = {"A": WearableButton.A, "B": WearableButton.B, "D": WearableButton.D}
        result = self.orchestrator.submit_handoff_answer(
            _answer(question_id=question.question_id, button=letter_to_button[letter], clock=self.clock)
        )
        self.assertTrue(result.accepted)


class ResearchFlowTest(OwnerHandoffE2ETestBase):
    def test_complete_fake_research_flow(self) -> None:
        self.orchestrator.start_task("task-research-1")
        self._confirm_leave()
        self._ask_and_answer("B")  # research direction
        self.assertEqual(self.orchestrator.routed_skill(), SkillKind.RESEARCH)

        result = self.orchestrator.execute_authorized_task()
        self.assertEqual(result.status, ExecutionStatus.COMPLETED)
        self.assertEqual(len(self.a2a_client.calls), 1)

        report = self.orchestrator.finalize_return(selected_task=self.RESEARCH_DIRECTION)
        self.assertEqual(report.status, "ready_for_review")
        self.orchestrator.deliver_control()
        self.assertEqual(self.store.get_task("task-research-1").state, S.RETURNED)
        self.assertFalse(self.orchestrator.active)


class CodingFlowNormalPathTest(OwnerHandoffE2ETestBase):
    def _drive_to_executing_coding_task(self) -> None:
        self.orchestrator.start_task("task-coding-1")
        self._confirm_leave()
        self._ask_and_answer("A")  # coding direction
        self.assertEqual(self.orchestrator.routed_skill(), SkillKind.CODING)

        manifest = self.orchestrator.begin_coding_workspace_duplication()
        self.assertTrue(Path(manifest.duplicate_path).exists())
        question = self.orchestrator.request_codex_data_authorization(context_summary="AI Desk")
        self.assertEqual(self.store.get_task("task-coding-1").state, S.PERMISSION_PENDING)

        result = self.orchestrator.submit_codex_data_answer(
            _answer(question_id=question.question_id, button=WearableButton.YES, clock=self.clock)
        )
        self.assertTrue(result.accepted)
        self.assertEqual(self.store.get_task("task-coding-1").state, S.EXECUTING)

    def test_complete_fake_coding_flow_with_duplicate_and_codex_data_yes(self) -> None:
        self._drive_to_executing_coding_task()
        result = self.orchestrator.execute_coding_task()
        self.assertEqual(result.status, ExecutionStatus.COMPLETED)
        self.assertEqual(self.coding_executor.call_count, 1)

        report = self.orchestrator.finalize_return(
            selected_task=self.CODING_DIRECTION, work_completed=("fixed the test",)
        )
        self.assertEqual(report.status, "ready_for_review")
        self.assertEqual(report.original_files_modified, ())  # original workspace unchanged
        self.assertEqual((self.source / "main.py").read_text(), "print('original')\n")
        self.orchestrator.deliver_control()
        self.assertEqual(self.store.get_task("task-coding-1").state, S.RETURNED)

    def test_accurate_files_created_and_modified_report(self) -> None:
        self.orchestrator = self._build_orchestrator(coding_executor=_WritingFakeCodingExecutor())
        self._drive_to_executing_coding_task()
        self.orchestrator.execute_coding_task()
        report = self.orchestrator.finalize_return(selected_task=self.CODING_DIRECTION)
        self.assertIn("NEW_FILE.txt", report.files_created)
        self.assertIn("main.py", report.files_modified_in_duplicate)
        self.assertNotIn("main.py", report.original_files_modified)
        self.assertEqual((self.source / "main.py").read_text(), "print('original')\n")

    def test_deletion_of_duplicate_main_py_fails_closed_never_ready_for_review(self) -> None:
        """Repair: deleting a copied file inside the duplicate must be
        detected by finalize_return and must never produce a normal
        ready-for-review report -- the task fails closed to FAILED."""

        from application.owner_handoff.domain.resume import ResumeReport, SafetyFailureRecord

        self._drive_to_executing_coding_task()
        self.orchestrator.execute_coding_task()
        (self.orchestrator._task_state.duplicate_path / "main.py").unlink()

        result = self.orchestrator.finalize_return(selected_task=self.CODING_DIRECTION)
        self.assertIsInstance(result, SafetyFailureRecord)
        self.assertNotIsInstance(result, ResumeReport)
        self.assertEqual(self.store.get_task("task-coding-1").state, S.FAILED)
        self.assertIn("main.py", result.reason)


class CodingFlowDenialTest(OwnerHandoffE2ETestBase):
    def _drive_to_permission_pending(self) -> None:
        self.orchestrator.start_task("task-coding-deny")
        self._confirm_leave()
        self._ask_and_answer("A")
        self.orchestrator.begin_coding_workspace_duplication()
        return self.orchestrator.request_codex_data_authorization(context_summary="AI Desk")

    def test_codex_data_no_cancels_and_executor_never_called(self) -> None:
        question = self._drive_to_permission_pending()
        result = self.orchestrator.submit_codex_data_answer(
            _answer(question_id=question.question_id, button=WearableButton.NO, clock=self.clock)
        )
        self.assertTrue(result.accepted)
        self.assertEqual(self.store.get_task("task-coding-deny").state, S.CANCELED)
        self.assertEqual(self.coding_executor.call_count, 0)

    def test_permission_timeout_cancels_and_executor_never_called(self) -> None:
        self._drive_to_permission_pending()
        self.clock.advance(61.0)
        record = self.orchestrator.cancel_pending_for_timeout(now=self.clock())
        self.assertEqual(record.state, S.CANCELED)
        self.assertEqual(self.coding_executor.call_count, 0)

    def test_stale_question_id_rejected_and_executor_never_called(self) -> None:
        self._drive_to_permission_pending()
        result = self.orchestrator.submit_codex_data_answer(
            _answer(question_id="wrong-id", button=WearableButton.YES, clock=self.clock)
        )
        self.assertFalse(result.accepted)
        self.assertEqual(self.store.get_task("task-coding-deny").state, S.PERMISSION_PENDING)
        self.assertEqual(self.coding_executor.call_count, 0)

    def test_wearable_disconnect_cancels_and_executor_never_called(self) -> None:
        self._drive_to_permission_pending()
        self.wearable.disconnect()
        record = self.orchestrator.cancel_pending_for_disconnect()
        self.assertEqual(record.state, S.CANCELED)
        self.assertEqual(self.coding_executor.call_count, 0)

    def test_owner_return_before_permission_cancels_and_executor_never_called(self) -> None:
        self._drive_to_permission_pending()
        record = self.orchestrator.request_return()
        self.assertEqual(record.state, S.CANCELED)
        self.assertEqual(self.coding_executor.call_count, 0)

    def test_disconnect_answer_still_rejected_and_no_permit_recorded(self) -> None:
        question = self._drive_to_permission_pending()
        self.wearable.disconnect()
        self.lifecycle.disconnect()
        result = self.orchestrator.submit_codex_data_answer(
            _answer(question_id=question.question_id, button=WearableButton.YES, clock=self.clock)
        )
        self.assertFalse(result.accepted)
        self.assertEqual(self.coding_executor.call_count, 0)


class DoNothingAndUnsupportedTest(OwnerHandoffE2ETestBase):
    def test_option_d_never_executes(self) -> None:
        self.orchestrator.start_task("task-d")
        self._confirm_leave()
        question = self.orchestrator.ask_question()
        result = self.orchestrator.submit_handoff_answer(
            _answer(question_id=question.question_id, button=WearableButton.D, clock=self.clock)
        )
        self.assertTrue(result.accepted)
        self.assertEqual(self.store.get_task("task-d").state, S.CANCELED)
        self.assertEqual(self.coding_executor.call_count, 0)
        self.assertEqual(len(self.a2a_client.calls), 0)

    def test_unsupported_route_never_executes(self) -> None:
        # Force a context whose only direction is ambiguous (no coding or
        # research verb+object co-occurrence) so routing resolves to
        # UNSUPPORTED for a real, non-D letter.
        context = WorkContext(
            possible_next_steps=("Look at the thing", "Consider the situation further"),
            confidence=0.4, updated_at=NOW,
        )
        self.work_context_tracker = WorkContextTracker(clock=self.clock, context=context)
        self.orchestrator = self._build_orchestrator()
        self.orchestrator.start_task("task-unsupported")
        self._confirm_leave()
        question = self.orchestrator.ask_question()
        result = self.orchestrator.submit_handoff_answer(
            _answer(question_id=question.question_id, button=WearableButton.A, clock=self.clock)
        )
        self.assertTrue(result.accepted)
        self.assertEqual(self.store.get_task("task-unsupported").state, S.AUTHORIZED)
        self.assertEqual(self.orchestrator.routed_skill(), SkillKind.UNSUPPORTED)

        exec_result = self.orchestrator.execute_authorized_task()
        self.assertEqual(exec_result.status, ExecutionStatus.CANCELED)
        self.assertEqual(self.store.get_task("task-unsupported").state, S.CANCELED)
        self.assertEqual(self.coding_executor.call_count, 0)
        self.assertEqual(len(self.a2a_client.calls), 0)


class PackageInstallFlowTest(OwnerHandoffE2ETestBase):
    def _drive_to_executing(self) -> None:
        self.orchestrator.start_task("task-pkg")
        self._confirm_leave()
        self._ask_and_answer("A")
        self.orchestrator.begin_coding_workspace_duplication()
        codex_question = self.orchestrator.request_codex_data_authorization(context_summary="AI Desk")
        self.orchestrator.submit_codex_data_answer(
            _answer(question_id=codex_question.question_id, button=WearableButton.YES, clock=self.clock)
        )
        self.assertEqual(self.store.get_task("task-pkg").state, S.EXECUTING)

    def test_package_install_exact_yes_flow(self) -> None:
        self._drive_to_executing()
        question = self.orchestrator.request_package_install_authorization(
            context_summary="AI Desk", package_specs=["requests==2.31.0"]
        )
        self.assertEqual(self.store.get_task("task-pkg").state, S.PERMISSION_PENDING)
        result = self.orchestrator.submit_package_install_answer(
            _answer(question_id=question.question_id, button=WearableButton.YES, clock=self.clock)
        )
        self.assertTrue(result.accepted)
        self.assertEqual(self.store.get_task("task-pkg").state, S.EXECUTING)

        self.assertEqual(len(self.install_runner.calls), 0)
        outcome = self.orchestrator.install_package()
        self.assertTrue(outcome.succeeded)
        self.assertEqual(len(self.install_runner.calls), 1)

    def test_package_mismatch_rejected(self) -> None:
        self._drive_to_executing()
        question = self.orchestrator.request_package_install_authorization(
            context_summary="AI Desk", package_specs=["requests==2.31.0"]
        )
        self.orchestrator.submit_package_install_answer(
            _answer(question_id=question.question_id, button=WearableButton.YES, clock=self.clock)
        )
        # Directly consume with a different (unauthorized) package spec via
        # the gate to prove the exact-match binding at the permission layer.
        with self.assertRaises(PermissionNotGrantedError):
            self.permission_gate.consume(
                task_id="task-pkg", permission_kind=PermissionKind.PACKAGE_INSTALL,
                session_id=self.orchestrator._task_state.manifest.session_id,
                duplicate_path=self.orchestrator._task_state.duplicate_path,
                live_digest="deadbeef", now=self.clock(),
                question_id=question.question_id,
                package_specs=("numpy==1.26.0",), argv=("numpy-install-argv",),
            )
        self.assertEqual(len(self.install_runner.calls), 0)

    def test_permit_reuse_rejected(self) -> None:
        self._drive_to_executing()
        question = self.orchestrator.request_package_install_authorization(
            context_summary="AI Desk", package_specs=["requests==2.31.0"]
        )
        self.orchestrator.submit_package_install_answer(
            _answer(question_id=question.question_id, button=WearableButton.YES, clock=self.clock)
        )
        self.orchestrator.install_package()
        self.assertEqual(len(self.install_runner.calls), 1)
        from application.owner_handoff.execution.package_installer import PackageInstallRejected

        with self.assertRaises(PackageInstallRejected):
            self.orchestrator.install_package()
        self.assertEqual(len(self.install_runner.calls), 1)


class ReturnDuringExecutionTest(OwnerHandoffE2ETestBase):
    def test_return_during_execution_stops_after_current_atomic_step(self) -> None:
        self.orchestrator.start_task("task-return-mid")
        self._confirm_leave()
        self._ask_and_answer("B")  # research: EXECUTING directly, no duplicate needed

        release = threading.Event()

        class _BlockingResearchExecutor:
            def execute(self, *, goal_text, expected_output=None):
                from application.owner_handoff.domain.execution import ExecutionResult

                release.wait(timeout=1.0)
                return ExecutionResult(status=ExecutionStatus.COMPLETED, summary="finished")

        self.orchestrator._research_executor = _BlockingResearchExecutor()
        self.orchestrator._store.apply_transition(
            task_id="task-return-mid", event_id="research-executing:task-return-mid",
            from_state=S.AUTHORIZED, to_state=S.EXECUTING, reason="research skill: no physical duplication needed",
        )

        # Owner returns while the step is (about to be) in flight.
        self.orchestrator.request_return()
        self.assertEqual(self.store.get_task("task-return-mid").state, S.RETURN_REQUESTED)

        def _run_step():
            return self.orchestrator._return_coordinator.run_current_step(
                lambda: self.orchestrator._research_executor.execute(goal_text="x"),
                task_id="task-return-mid",
            )

        timer = threading.Timer(0.02, release.set)
        timer.start()
        result = _run_step()
        self.assertEqual(result.status, ExecutionStatus.COMPLETED)

        report = self.orchestrator.finalize_return(selected_task=self.RESEARCH_DIRECTION)
        self.assertEqual(report.status, "ready_for_review")
        self.orchestrator.deliver_control()
        self.assertEqual(self.store.get_task("task-return-mid").state, S.RETURNED)


class OnlyOneActiveTaskTest(OwnerHandoffE2ETestBase):
    def test_second_task_rejected_while_one_is_active(self) -> None:
        self.orchestrator.start_task("task-first")
        with self.assertRaises(OrchestratorBusyError):
            self.orchestrator.start_task("task-second")

    def test_new_task_allowed_after_deliver_control(self) -> None:
        self.orchestrator.start_task("task-first")
        self._confirm_leave()
        self._ask_and_answer("B")
        self.orchestrator.execute_authorized_task()
        self.orchestrator.finalize_return(selected_task=self.RESEARCH_DIRECTION)
        self.orchestrator.deliver_control()
        self.orchestrator.start_task("task-second")
        self.assertEqual(self.orchestrator.task_id, "task-second")


class RestartRecoveryTest(OwnerHandoffE2ETestBase):
    def test_permission_pending_recovered_to_failed_on_restart(self) -> None:
        self.orchestrator.start_task("task-restart")
        self._confirm_leave()
        self._ask_and_answer("A")
        self.orchestrator.begin_coding_workspace_duplication()
        self.orchestrator.request_codex_data_authorization(context_summary="AI Desk")
        self.assertEqual(self.store.get_task("task-restart").state, S.PERMISSION_PENDING)

        recovered = self.orchestrator.recover_after_restart()
        self.assertEqual(self.store.get_task("task-restart").state, S.FAILED)
        self.assertTrue(any(r.task_id == "task-restart" for r in recovered))
        self.assertEqual(self.coding_executor.call_count, 0)


class RestartPersistenceTest(OwnerHandoffE2ETestBase):
    """Repair 7: restart in READY_FOR_REVIEW can reload and display the
    exact persisted report; restart in RETURN_REQUESTED fails closed."""

    def test_ready_for_review_report_survives_a_simulated_restart(self) -> None:
        from application.owner_handoff.persistence import TaskArtifactStore

        artifact_store = TaskArtifactStore(session_root=self.session_root)
        self.orchestrator = self._build_orchestrator(artifact_store=artifact_store)
        self.orchestrator.start_task("task-restart-report")
        self._confirm_leave()
        self._ask_and_answer("B")  # research
        self.orchestrator.execute_authorized_task()
        original_report = self.orchestrator.finalize_return(selected_task=self.RESEARCH_DIRECTION)
        self.assertEqual(self.store.get_task("task-restart-report").state, S.READY_FOR_REVIEW)

        # Simulate a process restart: a brand-new orchestrator instance,
        # sharing only the persisted store/session_root, attaches to the
        # existing task and reloads the report.
        fresh_orchestrator = self._build_orchestrator(artifact_store=artifact_store)
        fresh_orchestrator.attach_to_task("task-restart-report")
        reloaded = fresh_orchestrator.load_persisted_final_report()
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded["selected_task"], original_report.selected_task)
        self.assertEqual(reloaded["status"], "ready_for_review")

    def test_restart_while_return_requested_fails_closed(self) -> None:
        self.orchestrator.start_task("task-restart-return-requested")
        self._confirm_leave()
        self._ask_and_answer("B")
        self.orchestrator.execute_authorized_task()
        self.orchestrator.request_return()
        self.assertEqual(
            self.store.get_task("task-restart-return-requested").state, S.RETURN_REQUESTED
        )

        recovered = self.orchestrator.recover_after_restart()
        self.assertEqual(
            self.store.get_task("task-restart-return-requested").state, S.FAILED
        )
        self.assertTrue(any(r.task_id == "task-restart-return-requested" for r in recovered))


class NoRoutedOptionErrorTest(OwnerHandoffE2ETestBase):
    def test_execute_authorized_task_before_routing_fails_closed(self) -> None:
        self.orchestrator.start_task("task-early")
        with self.assertRaises(NoRoutedOptionError):
            self.orchestrator.execute_authorized_task()


class DemoCLISmokeTest(unittest.TestCase):
    """Exercises the real demo CLI module end-to-end (in-process, not via
    subprocess) against the checked-in demo_workspace fixture -- fake
    executor/research only, no hardware, network, Codex, or OpenAI key."""

    def setUp(self) -> None:
        import importlib.util
        import sys as _sys

        repo_root = Path(__file__).resolve().parent.parent
        self.demo_workspace = repo_root / "demo_workspace" / "sample_project"
        self.temp_dir = tempfile.TemporaryDirectory()
        self.session_root = Path(self.temp_dir.name) / "sessions"
        self.db_path = Path(self.temp_dir.name) / "db.sqlite3"

        spec = importlib.util.spec_from_file_location(
            "run_owner_handoff_demo", repo_root / "run_owner_handoff_demo.py"
        )
        module = importlib.util.module_from_spec(spec)
        _sys.modules["run_owner_handoff_demo"] = module
        spec.loader.exec_module(module)
        self.demo = module

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_demo_workspace_fixture_has_the_expected_bug(self) -> None:
        import subprocess
        import sys as _sys

        result = subprocess.run(
            [_sys.executable, "-B", "-m", "unittest", "-v"],
            cwd=self.demo_workspace, capture_output=True, text=True,
        )
        self.assertIn("test_add", result.stderr)
        self.assertNotEqual(result.returncode, 0)  # test_add fails on the seeded bug

    def test_full_coding_flow_through_demo_cli_no_network_or_hardware(self) -> None:
        import io

        original_calculator_source = (self.demo_workspace / "calculator.py").read_text()

        ctx = self.demo.build_demo_orchestrator(
            workspace=self.demo_workspace,
            session_root=self.session_root,
            db_path=self.db_path,
            executor_mode="fake",
            research_mode="fake",
        )
        out = io.StringIO()
        ok = self.demo.run_commands(
            ctx,
            [
                "start demo-smoke-task",
                "away",
                "ask",
                "select A",
                "grant",
                "run",
                "status",
                "wait",
                "finalize Fixed the calculator bug",
                "deliver",
            ],
            out,
        )
        text = out.getvalue()
        self.assertTrue(ok)
        self.assertIn("OWNER_LEFT_CONFIRMED", text)
        self.assertIn("routed skill: coding", text)
        self.assertIn("execution result: completed", text)
        self.assertIn("fixed calculator.py", text)
        self.assertIn('"status": "ready_for_review"', text)
        self.assertIn("control returned to owner", text)
        self.assertNotIn("error:", text)
        # The original demo fixture must remain byte-for-byte unchanged --
        # the fix landed only in the duplicate.
        self.assertEqual(
            (self.demo_workspace / "calculator.py").read_text(), original_calculator_source
        )

    def test_missing_workspace_argument_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            self.demo.build_demo_orchestrator(session_root=self.session_root)  # workspace omitted


class DemoRobustnessTest(unittest.TestCase):
    """Repair: the demo CLI must never crash on D/UNSUPPORTED, must allow a
    new task after a terminal one is acknowledged, must report concise
    errors (never a raw traceback) for out-of-order commands, and scripted
    mode must return a non-zero-equivalent (False) result when a command
    fails."""

    def setUp(self) -> None:
        import importlib.util
        import sys as _sys

        repo_root = Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location(
            "run_owner_handoff_demo", repo_root / "run_owner_handoff_demo.py"
        )
        module = importlib.util.module_from_spec(spec)
        _sys.modules["run_owner_handoff_demo"] = module
        spec.loader.exec_module(module)
        self.demo = module

        self.temp_dir = tempfile.TemporaryDirectory()
        self.boundary = Path(self.temp_dir.name)
        self.source = self.boundary / "project"
        self.source.mkdir()
        (self.source / "main.py").write_text("print(1)\n")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _build(self):
        return self.demo.build_demo_orchestrator(
            workspace=self.source,
            session_root=self.boundary / "sessions",
            db_path=self.boundary / "db.sqlite3",
        )

    def _run(self, ctx, commands):
        import io

        out = io.StringIO()
        ok = self.demo.run_commands(ctx, commands, out)
        return ok, out.getvalue()

    def test_select_d_never_crashes_and_cancels_cleanly(self) -> None:
        ctx = self._build()
        ok, text = self._run(
            ctx, ["start demo-d", "away", "ask", "select D"]
        )
        self.assertTrue(ok)
        self.assertNotIn("Traceback", text)
        self.assertEqual(
            ctx.orchestrator._store.get_task("demo-d").state, S.CANCELED
        )

    def test_unsupported_route_transitions_to_canceled_through_demo(self) -> None:
        from application.owner_handoff.domain.work_context import WorkContext, WorkContextTracker

        ctx = self._build()
        ctx.orchestrator._work_context_tracker = WorkContextTracker(
            context=WorkContext(
                possible_next_steps=("Look at the thing", "Consider the situation further"),
                confidence=0.4,
            )
        )
        ok, text = self._run(ctx, ["start demo-unsup", "away", "ask", "select A"])
        self.assertTrue(ok)
        self.assertNotIn("Traceback", text)
        self.assertIn("nothing executed", text)
        self.assertEqual(
            ctx.orchestrator._store.get_task("demo-unsup").state, S.CANCELED
        )

    def test_status_safe_when_no_task_active(self) -> None:
        ctx = self._build()
        ok, text = self._run(ctx, ["status"])
        self.assertTrue(ok)
        self.assertIn("no active task", text)

    def test_out_of_order_command_produces_concise_error_not_traceback(self) -> None:
        ctx = self._build()
        # 'select' before any task has even been started.
        ok, text = self._run(ctx, ["select A"])
        self.assertFalse(ok)
        self.assertIn("error:", text)
        self.assertNotIn("Traceback", text)

    def test_scripted_mode_reports_failure_when_a_command_errors(self) -> None:
        ctx = self._build()
        ok, _ = self._run(ctx, ["start demo-x", "grant"])  # grant with nothing pending
        self.assertFalse(ok)

    def test_acknowledge_allows_a_new_task_after_cancellation(self) -> None:
        ctx = self._build()
        ok, text = self._run(
            ctx, ["start demo-first", "away", "ask", "select D", "acknowledge", "start demo-second"]
        )
        self.assertTrue(ok)
        self.assertNotIn("error:", text)
        self.assertEqual(ctx.orchestrator.task_id, "demo-second")

    def test_demo_calculator_fix_executor_creates_marker_when_no_known_bug(self) -> None:
        """P5.1: against a workspace that doesn't contain the known
        calculator.py bug, the demo executor creates one clearly-named
        file instead of guessing at an arbitrary edit."""

        (self.source / "unrelated.py").write_text("x = 1\n")
        ok, text = self._run(
            ctx := self._build(),
            ["start demo-nomatch", "away", "ask", "select A", "grant", "run", "wait"],
        )
        self.assertTrue(ok)
        duplicate = ctx.orchestrator._task_state.duplicate_path
        self.assertTrue((duplicate / "AI_GENERATED_NOTE.txt").exists())
        self.assertIn("execution result: completed", text)

    def test_packages_installed_automatically_appear_in_resume_report(self) -> None:
        ctx = self._build()
        ok, text = self._run(
            ctx,
            [
                "start demo-pkg", "away", "ask", "select A", "grant", "run", "wait",
                "install requests==2.31.0", "grant", "return",
                "finalize Installed a dependency", "deliver",
            ],
        )
        self.assertTrue(ok)
        self.assertIn('"packages_installed": [', text)
        self.assertIn("requests==2.31.0", text)

    def test_second_task_rejected_before_acknowledge(self) -> None:
        ctx = self._build()
        ok, text = self._run(
            ctx, ["start demo-first", "away", "ask", "select D", "start demo-second"]
        )
        self.assertFalse(ok)
        self.assertIn("error:", text)
        self.assertEqual(ctx.orchestrator.task_id, "demo-first")


class DemoPreflightOnlyTest(unittest.TestCase):
    """P5.4: --preflight-only must run only the two inert preflight
    commands and never create a duplicate/task-db/CODEX_DATA question/A2A
    contact -- proven here by never even constructing an orchestrator."""

    def setUp(self) -> None:
        import importlib.util
        import sys as _sys

        repo_root = Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location(
            "run_owner_handoff_demo", repo_root / "run_owner_handoff_demo.py"
        )
        module = importlib.util.module_from_spec(spec)
        _sys.modules["run_owner_handoff_demo"] = module
        spec.loader.exec_module(module)
        self.demo = module

    def test_preflight_only_reports_supported_flags_with_fake_runner(self) -> None:
        import io

        from application.owner_handoff.execution.codex_cli import CommandResult

        help_text = (
            "codex exec [OPTIONS]\n--sandbox <MODE>\n--cd <DIR>\n--ephemeral\n"
            "--json\n--ignore-user-config\n--skip-git-repo-check\n"
            "-c, --config <KEY=VALUE>\n--strict-config\n"
        )

        class _FakeRunner:
            def __init__(self):
                self.calls = []

            def run(self, argv):
                self.calls.append(argv)
                if argv[1:] == ["--version"]:
                    return CommandResult(0, "codex-cli 0.999.0\n")
                return CommandResult(0, help_text)

        runner = _FakeRunner()
        out = io.StringIO()
        ok = self.demo.run_preflight_only(codex_binary="codex", runner=runner, out=out)
        self.assertTrue(ok)
        self.assertIn("codex-cli 0.999.0", out.getvalue())
        self.assertIn("all required isolation flags are supported", out.getvalue())
        # Only the two inert commands were ever run -- never `codex exec`
        # without `--help`, never a real duplicate/task-db/A2A contact
        # (none of those components were even constructed).
        self.assertEqual(len(runner.calls), 2)
        self.assertEqual([c[1:] for c in runner.calls], [["--version"], ["exec", "--help"]])

    def test_preflight_only_reports_missing_flags(self) -> None:
        import io

        from application.owner_handoff.execution.codex_cli import CommandResult

        class _FakeRunner:
            def run(self, argv):
                if argv[1:] == ["--version"]:
                    return CommandResult(0, "codex-cli 0.1.0\n")
                return CommandResult(0, "codex exec [OPTIONS]\n--json\n")

        out = io.StringIO()
        ok = self.demo.run_preflight_only(codex_binary="codex", runner=_FakeRunner(), out=out)
        self.assertFalse(ok)
        # CodexPreflight.run() itself already fails closed (raises) when a
        # required flag isn't advertised -- run_preflight_only reports that
        # plainly rather than claiming success.
        self.assertIn("preflight failed", out.getvalue())

    def test_preflight_only_never_touches_orchestrator_components(self) -> None:
        """Structural proof: run_preflight_only never imports/constructs
        OwnerHandoffStore, duplicate_workspace, or PermissionGate --
        it only ever calls CodexPreflight.run()."""

        import io
        import unittest.mock as mock

        from application.owner_handoff.execution.codex_cli import CommandResult

        class _FakeRunner:
            def run(self, argv):
                if argv[1:] == ["--version"]:
                    return CommandResult(1, "", "not found")
                return CommandResult(0, "")

        with mock.patch(
            "application.owner_handoff.store.OwnerHandoffStore.__init__",
            side_effect=AssertionError("must never construct a store in preflight-only mode"),
        ):
            out = io.StringIO()
            ok = self.demo.run_preflight_only(codex_binary="codex", runner=_FakeRunner(), out=out)
            self.assertFalse(ok)

    def test_main_loads_repository_dotenv_before_building_demo(self) -> None:
        """The documented macOS `.env` setup must not be a no-op."""

        import unittest.mock as mock

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "project"
            source.mkdir()
            (source / "main.py").write_text("print('demo')\n")
            script = root / "commands.txt"
            script.write_text("")

            with mock.patch.object(
                self.demo, "load_application_environment", return_value=True
            ) as load_env:
                result = self.demo.main(
                    [
                        "--workspace", str(source),
                        "--allowed-boundary", str(root),
                        "--session-root", str(root / "sessions"),
                        "--db-path", str(root / "owner_handoff.sqlite3"),
                        "--script", str(script),
                    ]
                )

        self.assertEqual(result, 0)
        load_env.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
