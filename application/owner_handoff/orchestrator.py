"""Top-level AI Desk V2 orchestrator (Master Spec, Phase 4).

Composes every existing Phase 1-3 component through dependency injection
behind deterministic, explicitly-called methods -- there is no background
thread, poller, or sleep anywhere in this class; a caller (a real event
loop, or a test) decides when each tick/event happens.

Required flow (enforced by ``state_machine.py``'s transition table, driven
here):

    OBSERVING -> LEFT_CANDIDATE -> OWNER_LEFT_CONFIRMED -> QUESTION_PENDING
    -> AUTHORIZED
    -> RESEARCH: EXECUTING
    or
    -> CODING: WORKSPACE_DUPLICATING -> PERMISSION_PENDING(CODEX_DATA) -> EXECUTING
    -> RETURN_REQUESTED (when applicable)
    -> READY_FOR_REVIEW -> RETURNED

Design decision (documented, not hidden): ``QuestionLifecycle.submit_answer``
only knows "was D selected or not" -- it authorizes AUTHORIZED for ANY
non-D letter, regardless of what that option's text routes to. Routing
safety is therefore enforced here, one layer up: ``execute_authorized_task``
checks the routed ``SkillKind`` before any duplication or execution begins,
and cancels (never executes) an UNSUPPORTED or DO_NOTHING route. This keeps
``HandoffQuestion``'s external JSON contract, and the lifecycle's own
authorization semantics, completely unchanged.

Only one task may be active through this orchestrator instance at a time
(the Master Spec's "only one active handoff task may execute at a time in
the MVP" rule) -- ``start_task`` raises if a task is already active.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from application.owner_handoff.domain.execution import ExecutionResult, ExecutionStatus, SkillKind
from application.owner_handoff.domain.manifest import DuplicationManifest
from application.owner_handoff.domain.presence import WearableAnswer
from application.owner_handoff.domain.question import HandoffQuestion, PermissionQuestion
from application.owner_handoff.domain.resume import ResumeReport, SafetyFailureRecord
from application.owner_handoff.domain.work_context import WorkContextTracker, redact_sensitive
from application.owner_handoff.persistence import TaskArtifactStore
from application.owner_handoff.execution.coding_executor import CodingAgentExecutor, CodingTaskRequest
from application.owner_handoff.execution.package_installer import PackageInstallOutcome, PackageInstaller
from application.owner_handoff.execution.permission import PermissionGate, compute_manifest_hash
from application.owner_handoff.execution.permission import (
    request_codex_data_permission,
    request_package_install_permission,
)
from application.owner_handoff.execution.research_executor import ResearchAgentExecutor
from application.owner_handoff.fusion.leave_detector import (
    LeaveDetector,
    advance_state_machine_for_leave_evaluation,
)
from application.owner_handoff.fusion.return_detector import ReturnDetector
from application.owner_handoff.questions.generator import HandoffQuestionGenerator
from application.owner_handoff.questions.lifecycle import (
    AnswerSubmissionResult,
    QuestionKind,
    QuestionLifecycle,
    cancel_for_disconnect,
    cancel_for_owner_return,
    cancel_for_timeout,
    recover_pending_questions_after_restart,
)
from application.owner_handoff.questions.terminal import wearable_payload
from application.owner_handoff.routing.skills import route_handoff_question
from application.owner_handoff.state_machine import OwnerHandoffState, PermissionKind
from application.owner_handoff.store import OwnerHandoffStore, StaleTransitionError, TaskRecord
from application.owner_handoff.workspace.duplicator import duplicate_workspace, scan_duplicate_changes
from application.owner_handoff.adapters.radar import RadarSensor
from application.owner_handoff.adapters.wearable import WearableTransport
from application.owner_handoff.return_coordinator import (
    ReturnCoordinator,
    StepDidNotFinishInGracePeriodError,
)
from utils.time_utils import utc_now

S = OwnerHandoffState


class OrchestratorError(RuntimeError):
    """Base error for orchestrator misuse (always fail-closed)."""


class OrchestratorBusyError(OrchestratorError):
    """Raised by ``start_task`` when a task is already active -- only one
    active handoff task may execute at a time in this MVP."""


class NoRoutedOptionError(OrchestratorError):
    """Raised when execution is requested before a real (non-D) option has
    been authorized."""


@dataclass
class _TaskState:
    """Mutable per-active-task scratch state. Reset on every ``start_task``
    and cleared on ``deliver_control``."""

    question: HandoffQuestion | None = None
    plan: object | None = None
    chosen_letter: str | None = None
    manifest: DuplicationManifest | None = None
    duplicate_path: Path | None = None
    codex_permission_question: PermissionQuestion | None = None
    package_permission_question: PermissionQuestion | None = None
    package_specs: tuple[str, ...] = ()
    package_argv: tuple[str, ...] = ()
    last_execution_result: ExecutionResult | None = None
    # Accumulates the exact normalized package specs from every
    # successfully permitted install this task has performed -- never a
    # failed/denied/mismatched attempt. finalize_return() reads this
    # directly rather than trusting a caller-supplied list.
    packages_installed: tuple[str, ...] = ()


class OwnerHandoffOrchestrator:
    def __init__(
        self,
        *,
        store: OwnerHandoffStore,
        source_workspace: Path,
        allowed_boundary: Path,
        session_root: Path,
        leave_detector: LeaveDetector,
        return_detector: ReturnDetector,
        radar: RadarSensor,
        wearable: WearableTransport,
        work_context_tracker: WorkContextTracker,
        question_generator: HandoffQuestionGenerator,
        lifecycle: QuestionLifecycle,
        permission_gate: PermissionGate,
        research_executor: ResearchAgentExecutor,
        coding_executor: CodingAgentExecutor,
        package_installer: PackageInstaller,
        return_coordinator: ReturnCoordinator,
        question_expiration_seconds: float,
        coding_max_runtime_seconds: float,
        coding_max_safe_steps: int,
        clock: Callable[[], datetime] = utc_now,
        artifact_store: TaskArtifactStore | None = None,
    ) -> None:
        self._artifact_store = artifact_store
        self._store = store
        self._source_workspace = source_workspace
        self._allowed_boundary = allowed_boundary
        self._session_root = session_root
        self._leave_detector = leave_detector
        self._return_detector = return_detector
        self._radar = radar
        self._wearable = wearable
        self._work_context_tracker = work_context_tracker
        self._question_generator = question_generator
        self._lifecycle = lifecycle
        self._permission_gate = permission_gate
        self._research_executor = research_executor
        self._coding_executor = coding_executor
        self._package_installer = package_installer
        self._return_coordinator = return_coordinator
        self._question_expiration_seconds = question_expiration_seconds
        self._coding_max_runtime_seconds = coding_max_runtime_seconds
        self._coding_max_safe_steps = coding_max_safe_steps
        self._clock = clock

        self._task_id: str | None = None
        self._task_state = _TaskState()

    # -- lifecycle -----------------------------------------------------

    @property
    def task_id(self) -> str | None:
        return self._task_id

    @property
    def active(self) -> bool:
        return self._task_id is not None

    def current_state(self) -> OwnerHandoffState:
        if self._task_id is None:
            raise OrchestratorError("no active task")
        return self._store.get_task(self._task_id).state

    def start_task(self, task_id: str) -> TaskRecord:
        """Only one active handoff task may execute at a time -- refuses to
        start a second task while one is still active."""

        if self.active:
            raise OrchestratorBusyError(
                f"task {self._task_id!r} is still active; only one active "
                "handoff task may execute at a time"
            )
        record = self._store.create_task(task_id)
        self._task_id = task_id
        self._task_state = _TaskState()
        self._return_detector.reset()
        return record

    def acknowledge_terminal_task(self) -> None:
        """Frees this orchestrator for a new ``start_task`` call once the
        current task has reached a terminal state (CANCELED, FAILED, or
        RETURNED) by a path OTHER than the normal ``deliver_control()``
        flow -- e.g. a duplication failure, an executor failure, or a
        route that canceled immediately (D, UNSUPPORTED).

        ``deliver_control()`` already clears task state for the normal
        READY_FOR_REVIEW -> RETURNED happy path; this is the equivalent
        for the other terminal states, which ``deliver_control`` cannot
        reach (it only accepts a READY_FOR_REVIEW source). Raises
        ``OrchestratorError`` if the current task is not actually terminal
        yet -- a still-active task can only be freed by actually finishing
        or being explicitly canceled, never by fiat.
        """

        if self._task_id is None:
            return
        current = self.current_state()
        if current not in (S.CANCELED, S.FAILED, S.RETURNED):
            raise OrchestratorError(
                f"task {self._task_id!r} is not in a terminal state yet "
                f"(currently {current.value}); it cannot be acknowledged"
            )
        self._task_id = None
        self._task_state = _TaskState()

    def _fail_current_task(self, *, reason: str) -> None:
        """Best-effort transition of the current task to FAILED from
        whatever runtime-bound state it is currently in. A no-op if the
        task has already reached a terminal state (e.g. a concurrent
        return-driven transition beat this one to FAILED already) --
        never raises for that ordinary race, only logs nothing and
        returns."""

        if self._task_id is None:
            return
        current = self.current_state()
        if current in (S.CANCELED, S.FAILED, S.RETURNED):
            return
        try:
            self._store.apply_transition(
                task_id=self._task_id,
                event_id=f"failed:{self._task_id}:{current.value}",
                from_state=current,
                to_state=S.FAILED,
                reason=redact_sensitive(reason),
            )
        except StaleTransitionError:
            pass

    # -- presence / leave ------------------------------------------------

    def evaluate_presence_for_leave(
        self, *, mouse_active: bool = False, keyboard_active: bool = False
    ) -> None:
        """OBSERVING -> LEFT_CANDIDATE -> OWNER_LEFT_CONFIRMED, driven by the
        leave detector's fusion of the injected radar/wearable readings.

        ``mouse_active``/``keyboard_active`` mean "activity happened just
        now" -- they reset the detector's own idle timers (via
        ``record_mouse_activity``/``record_keyboard_activity``) rather than
        being passed straight through to ``evaluate``, which tracks idle
        state internally.
        """

        if mouse_active:
            self._leave_detector.record_mouse_activity()
        if keyboard_active:
            self._leave_detector.record_keyboard_activity()

        radar_state = self._radar.read().state
        wearable_state = self._wearable.read_proximity().state
        evaluation = self._leave_detector.evaluate(radar=radar_state, wearable=wearable_state)
        advance_state_machine_for_leave_evaluation(self._store, self._task_id, evaluation)

    def evaluate_presence_for_return(
        self, *, mouse_active: bool = False, keyboard_active: bool = False
    ) -> TaskRecord | None:
        """Confirms owner return (radar PERSON_PRESENT + wearable
        OWNER_NEAR) via the injected ``ReturnDetector`` and, if confirmed
        while EXECUTING, routes into ``request_return()``."""

        radar_state = self._radar.read().state
        wearable_state = self._wearable.read_proximity().state
        evaluation = self._return_detector.evaluate(
            radar=radar_state,
            wearable=wearable_state,
            mouse_active=mouse_active,
            keyboard_active=keyboard_active,
        )
        if evaluation.confirmed:
            return self.request_return()
        return None

    # -- question / routing ---------------------------------------------

    def ask_question(self) -> HandoffQuestion:
        """OWNER_LEFT_CONFIRMED -> QUESTION_PENDING; generates and sends the
        handoff question. The wearable only ever receives the minimal
        (question_id, letters) payload -- never the question text."""

        self._store.apply_transition(
            task_id=self._task_id,
            event_id=f"question-pending:{self._task_id}",
            from_state=S.OWNER_LEFT_CONFIRMED,
            to_state=S.QUESTION_PENDING,
            reason="asking handoff question",
        )
        question = self._question_generator.generate(self._work_context_tracker.context)
        self._task_state.question = question
        self._task_state.plan = route_handoff_question(question)
        self._lifecycle.start_question(question, kind=QuestionKind.HANDOFF_SELECTION)
        question_id, letters = wearable_payload(question)
        self._wearable.send_question(question_id, letters)
        return question

    def submit_handoff_answer(self, answer: WearableAnswer) -> AnswerSubmissionResult:
        result = self._lifecycle.submit_answer(answer, store=self._store, task_id=self._task_id)
        if result.accepted:
            # The question has now been resolved (either AUTHORIZED or,
            # for option D, CANCELED) -- it is no longer "pending" in the
            # state-machine sense, so free the lifecycle's single pending
            # slot for whatever question comes next (a permission question,
            # for a coding task).
            self._lifecycle.cancel_pending()
            if result.task_record is not None and result.task_record.state is S.AUTHORIZED:
                self._task_state.chosen_letter = answer.button.value
        return result

    def routed_skill(self) -> SkillKind:
        if self._task_state.plan is None or self._task_state.chosen_letter is None:
            raise NoRoutedOptionError("no option has been authorized yet")
        return self._task_state.plan.route_for(self._task_state.chosen_letter).skill

    def routed_goal_text(self) -> str:
        if self._task_state.plan is None or self._task_state.chosen_letter is None:
            raise NoRoutedOptionError("no option has been authorized yet")
        return self._task_state.plan.route_for(self._task_state.chosen_letter).goal_text

    # -- execution --------------------------------------------------------

    def execute_authorized_task(self) -> ExecutionResult:
        """Dispatches on the routed skill. UNSUPPORTED and DO_NOTHING never
        execute anything -- the task is canceled instead."""

        skill = self.routed_skill()
        if skill in (SkillKind.UNSUPPORTED, SkillKind.DO_NOTHING):
            self._store.apply_transition(
                task_id=self._task_id,
                event_id=f"unsupported-cancel:{self._task_id}",
                from_state=S.AUTHORIZED,
                to_state=S.CANCELED,
                reason=f"routed skill {skill.value} never executes",
            )
            result = ExecutionResult(
                status=ExecutionStatus.CANCELED,
                summary=f"routed skill {skill.value}; nothing executed",
            )
            self._task_state.last_execution_result = result
            return result
        if skill is SkillKind.RESEARCH:
            return self._execute_research()
        raise OrchestratorError(
            "coding tasks must go through begin_coding_workspace_duplication() "
            "and the CODEX_DATA permission flow, not execute_authorized_task()"
        )

    def _execute_research(self) -> ExecutionResult:
        self._store.apply_transition(
            task_id=self._task_id,
            event_id=f"research-executing:{self._task_id}",
            from_state=S.AUTHORIZED,
            to_state=S.EXECUTING,
            reason="research skill: no physical duplication needed",
        )
        try:
            result = self._return_coordinator.run_current_step(
                lambda: self._research_executor.execute(goal_text=self.routed_goal_text()),
                task_id=self._task_id,
            )
        except StepDidNotFinishInGracePeriodError as exc:
            self._fail_current_task(reason=f"research step did not finish in time: {exc}")
            raise
        self._task_state.last_execution_result = result
        if result.status is ExecutionStatus.FAILED:
            self._fail_current_task(reason=f"research execution failed: {result.summary}")
        return result

    # -- coding: duplication + CODEX_DATA permission ----------------------

    def begin_coding_workspace_duplication(self) -> DuplicationManifest:
        skill = self.routed_skill()
        if skill is not SkillKind.CODING:
            raise OrchestratorError(f"routed skill is {skill.value}, not coding")
        self._store.apply_transition(
            task_id=self._task_id,
            event_id=f"workspace-duplicating:{self._task_id}",
            from_state=S.AUTHORIZED,
            to_state=S.WORKSPACE_DUPLICATING,
            reason="coding skill: physically duplicating workspace",
        )
        try:
            duplicate_path, manifest = duplicate_workspace(
                source=self._source_workspace,
                allowed_boundary=self._allowed_boundary,
                session_root=self._session_root,
                task_id=self._task_id,
            )
        except Exception as exc:  # noqa: BLE001 -- any duplication failure fails the task closed
            self._fail_current_task(reason=f"workspace duplication failed: {exc}")
            raise
        self._task_state.duplicate_path = duplicate_path
        self._task_state.manifest = manifest
        return manifest

    def request_codex_data_authorization(self, *, context_summary: str) -> PermissionQuestion:
        question = self._permission_gate.build_codex_data_question(
            context_summary=context_summary,
            duplicate_path=str(self._task_state.duplicate_path),
            expiration_seconds=self._question_expiration_seconds,
            clock=self._clock,
        )
        request_codex_data_permission(self._store, self._task_id, question)
        self._task_state.codex_permission_question = question
        self._lifecycle.start_question(question, kind=QuestionKind.PERMISSION)
        self._wearable.send_question(question.question_id, ("YES", "NO"))
        return question

    def submit_codex_data_answer(self, answer: WearableAnswer) -> AnswerSubmissionResult:
        manifest = self._task_state.manifest
        duplicate_path = self._task_state.duplicate_path
        result = self._permission_gate.grant_from_answer(
            lifecycle=self._lifecycle,
            answer=answer,
            store=self._store,
            task_id=self._task_id,
            session_id=manifest.session_id,
            duplicate_path=duplicate_path,
            compute_digest=lambda: compute_manifest_hash(duplicate_path),
            clock=self._clock,
        )
        if result.accepted:
            self._lifecycle.cancel_pending()
        return result

    def execute_coding_task(self) -> ExecutionResult:
        manifest = self._task_state.manifest
        question = self._task_state.codex_permission_question
        task_id = self._task_id
        request = CodingTaskRequest(
            goal_text=self.routed_goal_text(),
            duplicate_path=self._task_state.duplicate_path,
            max_runtime_seconds=self._coding_max_runtime_seconds,
            max_safe_steps=self._coding_max_safe_steps,
            task_id=task_id,
            session_id=manifest.session_id,
            question_id=question.question_id,
            permission_gate=self._permission_gate,
            # Cooperative signal: a stop-aware executor (CodexCLIExecutor,
            # MultiStepFakeCodingExecutor) polls this between its own
            # internal steps and returns promptly once return is
            # confirmed, rather than relying solely on the
            # ReturnCoordinator's grace-period backstop.
            stop_requested=lambda: self._return_coordinator.is_return_requested(task_id),
        )
        terminate_fn = getattr(self._coding_executor, "terminate_current", None)
        try:
            result = self._return_coordinator.run_current_step(
                lambda: self._coding_executor.execute(request),
                task_id=task_id,
                terminate_fn=terminate_fn,
            )
        except StepDidNotFinishInGracePeriodError as exc:
            self._fail_current_task(reason=f"coding step did not finish in time: {exc}")
            raise
        self._task_state.last_execution_result = result
        if result.status is ExecutionStatus.FAILED:
            self._fail_current_task(reason=f"coding execution failed: {result.summary}")
        return result

    # -- coding: mid-execution PACKAGE_INSTALL permission -----------------

    def request_package_install_authorization(
        self, *, context_summary: str, package_specs: list[str]
    ) -> PermissionQuestion:
        duplicate_path = self._task_state.duplicate_path
        argv = self._package_installer.build_argv(
            package_specs=package_specs, duplicate_path=duplicate_path
        )
        question = self._permission_gate.build_package_install_question(
            context_summary=context_summary,
            package_names=tuple(package_specs),
            proposed_argv=tuple(argv),
            duplicate_path=str(duplicate_path),
            expiration_seconds=self._question_expiration_seconds,
            clock=self._clock,
        )
        request_package_install_permission(self._store, self._task_id, question)
        self._task_state.package_permission_question = question
        self._task_state.package_specs = tuple(package_specs)
        self._task_state.package_argv = tuple(argv)
        self._lifecycle.start_question(question, kind=QuestionKind.PERMISSION)
        self._wearable.send_question(question.question_id, ("YES", "NO"))
        return question

    def submit_package_install_answer(self, answer: WearableAnswer) -> AnswerSubmissionResult:
        manifest = self._task_state.manifest
        duplicate_path = self._task_state.duplicate_path
        result = self._permission_gate.grant_from_answer(
            lifecycle=self._lifecycle,
            answer=answer,
            store=self._store,
            task_id=self._task_id,
            session_id=manifest.session_id,
            duplicate_path=duplicate_path,
            compute_digest=lambda: compute_manifest_hash(duplicate_path),
            package_specs=self._task_state.package_specs,
            argv=self._task_state.package_argv,
            clock=self._clock,
        )
        if result.accepted:
            self._lifecycle.cancel_pending()
        return result

    def install_package(self) -> PackageInstallOutcome:
        manifest = self._task_state.manifest
        question = self._task_state.package_permission_question
        outcome = self._package_installer.install(
            package_specs=list(self._task_state.package_specs),
            duplicate_path=self._task_state.duplicate_path,
            task_id=self._task_id,
            session_id=manifest.session_id,
            question_id=question.question_id,
            permission_gate=self._permission_gate,
        )
        if outcome.succeeded:
            # Only a successful, permitted install is ever recorded -- a
            # failed/denied/mismatched attempt never appears here and can
            # never be presented as installed.
            self._task_state.packages_installed = self._task_state.packages_installed + tuple(
                self._task_state.package_specs
            )
            if self._artifact_store is not None:
                self._artifact_store.save_packages_installed(
                    self._task_id, self._task_state.packages_installed
                )
        return outcome

    # -- return / cancellation -------------------------------------------

    def cancel_pending_for_timeout(self, *, now: datetime) -> TaskRecord | None:
        pending = self._current_pending_question()
        if pending is None:
            return None
        record = cancel_for_timeout(self._store, self._task_id, pending, now=now)
        if record is not None:
            self._lifecycle.cancel_pending()
        return record

    def cancel_pending_for_disconnect(self) -> TaskRecord | None:
        pending = self._current_pending_question()
        if pending is None:
            return None
        record = cancel_for_disconnect(self._store, self._task_id, pending)
        self._lifecycle.cancel_pending()
        return record

    def request_return(self) -> TaskRecord | None:
        """Owner return, at whatever stage the task currently is:

        - before authorization (QUESTION_PENDING / PERMISSION_PENDING):
          cancels via the existing question-lifecycle helper, zero executor
          calls;
        - during EXECUTING: routes through ``ReturnCoordinator.request_return``
          (RETURN_REQUESTED), which stops any new step from starting.
        """

        current = self.current_state()
        if current is S.EXECUTING:
            self._return_coordinator.request_return(task_id=self._task_id)
            return self._store.get_task(self._task_id)
        pending = self._current_pending_question()
        if pending is not None:
            record = cancel_for_owner_return(self._store, self._task_id, pending)
            self._lifecycle.cancel_pending()
            return record
        return None

    def _current_pending_question(self) -> HandoffQuestion | PermissionQuestion | None:
        current = self.current_state()
        if current is S.QUESTION_PENDING:
            return self._task_state.question
        if current is S.PERMISSION_PENDING:
            return (
                self._task_state.package_permission_question
                or self._task_state.codex_permission_question
            )
        return None

    def finalize_return(
        self,
        *,
        selected_task: str,
        work_completed: tuple[str, ...] = (),
    ) -> ResumeReport | SafetyFailureRecord:
        """RETURN_REQUESTED/EXECUTING -> READY_FOR_REVIEW (verification
        passed) or -> FAILED (verification failed, or a prohibited
        duplicate-side change was detected). Never merges duplicate
        changes back into the original. Callers must check the returned
        type: only a ``ResumeReport`` means it is safe to call
        ``deliver_control`` next -- a ``SafetyFailureRecord`` means the
        task is already FAILED and ``deliver_control`` must not be called.

        ``packages_installed`` is never caller-supplied -- it always comes
        from ``self._task_state.packages_installed``, which only ever
        accumulates the exact specs from successful, permitted installs
        (see ``install_package``).

        Repair (persistence): the manifest, the routed selected task, and
        the last execution result are persisted (sanitized, atomic) BEFORE
        any READY_FOR_REVIEW/FAILED transition is attempted; the final
        report/failure itself is persisted immediately after being built,
        in the same synchronous call.
        """

        packages_installed = self._task_state.packages_installed
        if self._artifact_store is not None:
            try:
                skill_label = self.routed_skill().value
            except NoRoutedOptionError:
                skill_label = "unrouted"
            self._artifact_store.save_selected_task(
                self._task_id, selected_task=selected_task, skill=skill_label,
            )
            if self._task_state.manifest is not None:
                self._artifact_store.save_manifest(self._task_id, self._task_state.manifest)
            if self._task_state.last_execution_result is not None:
                self._artifact_store.save_execution_result(
                    self._task_id, label="coding_or_research",
                    result=self._task_state.last_execution_result,
                )

        manifest = self._task_state.manifest
        current = self.current_state()
        if manifest is None:
            # Research task: no physical duplicate exists to verify.
            self._store.apply_transition(
                task_id=self._task_id,
                event_id=f"ready-for-review:{self._task_id}",
                from_state=current,
                to_state=S.READY_FOR_REVIEW,
                reason="research task finished",
            )
            report = ResumeReport(
                selected_task=selected_task,
                work_completed=tuple(work_completed),
                files_created=(),
                files_modified_in_duplicate=(),
                original_files_modified=(),
                packages_installed=packages_installed,
                status="ready_for_review",
            )
            if self._artifact_store is not None:
                self._artifact_store.save_final_report(self._task_id, report)
            return report

        scanned = scan_duplicate_changes(manifest)
        if scanned.duplicate_unsafe:
            self._store.apply_transition(
                task_id=self._task_id,
                event_id=f"duplicate-unsafe:{self._task_id}",
                from_state=current,
                to_state=S.FAILED,
                reason=f"prohibited duplicate-workspace change detected: {scanned.duplicate_unsafe_reason}",
            )
            self._task_state.manifest = scanned
            failure = SafetyFailureRecord(
                selected_task=selected_task,
                reason=(
                    "prohibited change detected in the duplicate workspace: "
                    f"{scanned.duplicate_unsafe_reason}"
                ),
                original_files_modified=(),
            )
            if self._artifact_store is not None:
                self._artifact_store.save_final_report(self._task_id, failure)
            return failure

        report_or_failure, verified_manifest = self._return_coordinator.finalize(
            task_id=self._task_id,
            manifest=scanned,
            selected_task=selected_task,
            work_completed=work_completed,
            packages_installed=packages_installed,
            from_state=current,
        )
        self._task_state.manifest = verified_manifest
        if self._artifact_store is not None:
            self._artifact_store.save_final_report(self._task_id, report_or_failure)
        return report_or_failure

    def deliver_control(self) -> None:
        """READY_FOR_REVIEW -> RETURNED, and frees this orchestrator for a
        new ``start_task`` call (only one task active at a time)."""

        self._return_coordinator.deliver_control(task_id=self._task_id)
        self._task_id = None
        self._task_state = _TaskState()

    # -- restart recovery --------------------------------------------------

    def recover_after_restart(self) -> list[TaskRecord]:
        """Fail-closed restart recovery across every runtime-bound and
        pending-question state -- never silently resumes a task this
        process instance has no live handle for. RETURN_REQUESTED is a
        runtime-bound state (see ``state_machine.RUNTIME_BOUND_STATES``)
        and is therefore included here exactly like
        EXECUTING/WORKSPACE_DUPLICATING/PERMISSION_PENDING."""

        recovered = list(self._store.recover_after_restart())
        recovered.extend(recover_pending_questions_after_restart(self._store))
        return recovered

    def attach_to_task(self, task_id: str) -> TaskRecord:
        """Attach to an already-existing task record after a restart --
        e.g. to reload and display a persisted READY_FOR_REVIEW report.
        Never creates a new task and never resumes in-progress work; call
        ``recover_after_restart()`` first so any runtime-bound state has
        already failed closed. This orchestrator's in-memory
        ``_task_state`` (manifest, routing, etc.) is empty after
        attaching -- only artifact-store-backed reads
        (``load_persisted_final_report``) and store-backed reads
        (``current_state``) are meaningful until a new ``start_task``.
        """

        if self.active:
            raise OrchestratorBusyError(
                f"task {self._task_id!r} is still active; acknowledge or "
                "finish it before attaching to another task"
            )
        record = self._store.get_task(task_id)
        self._task_id = task_id
        self._task_state = _TaskState()
        return record

    def load_persisted_final_report(self) -> dict | None:
        """Reload the exact sanitized final report/failure record that was
        persisted before the current task's READY_FOR_REVIEW/FAILED
        transition -- e.g. after ``attach_to_task`` following a restart.
        Returns ``None`` if no artifact store is configured or nothing was
        ever persisted for this task."""

        if self._artifact_store is None or self._task_id is None:
            return None
        return self._artifact_store.load_final_report(self._task_id)
