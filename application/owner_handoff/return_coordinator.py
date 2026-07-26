"""Return coordination: EXECUTING -> RETURN_REQUESTED -> READY_FOR_REVIEW ->
RETURNED (Master Spec section 17-18).

Once a confirmed return arrives, this component's whole job is to stop
starting NEW work while letting whatever is already the single current
atomic step finish -- bounded by a documented grace period -- and then
produce the fixed-shape resume report. It never auto-merges or copies
duplicate-workspace changes back into the original workspace.

Repair (cancellable execution session, replacing a ``ThreadPoolExecutor``-
based implementation): a Python thread cannot be force-canceled, and
leaving a ``ThreadPoolExecutor`` context (or calling ``shutdown(wait=True)``,
which every context-manager exit does) blocks until every submitted task
finishes -- including one that never will. Worse, ``ThreadPoolExecutor``
registers an interpreter-exit hook that joins its worker threads even after
``shutdown(wait=False)``, so a stuck task can hang process exit itself.
``ExecutionSession`` instead runs ``step_fn`` on a plain ``daemon=True``
thread: every wait here (``wait(timeout)``) is bounded and returns even if
the thread is still running, and a still-running daemon thread can never
block interpreter exit. This is real fail-safety, not a smaller version of
the same bug: a genuinely uncooperative step (a real A2A call with no
cancellation API, or a test double that never returns) is *abandoned*, not
waited on -- ``run_current_step`` reports that honestly
(``StepDidNotFinishInGracePeriodError``) rather than claiming it was
terminated.

The "current atomic step" for this MVP is one
``CodingAgentExecutor.execute()`` (or ``ResearchAgentExecutor.execute()``)
call. A cooperative executor (``CodexCLIExecutor``, ``MultiStepFakeCodingExecutor``)
polls ``CodingTaskRequest.stop_requested`` between its own internal steps
and returns promptly once return is confirmed -- ``run_current_step``'s
grace period is the backstop for an executor that does not (or cannot)
cooperate, e.g. a real Codex process wedged in a single step, or a remote
A2A call. Tests drive this deterministically with an injectable,
event-based fake step (no real sleeps), never a real wall-clock wait.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Generic, TypeVar

from application.owner_handoff.domain.manifest import DuplicationManifest
from application.owner_handoff.domain.resume import ResumeReport, SafetyFailureRecord
from application.owner_handoff.state_machine import OwnerHandoffState
from application.owner_handoff.store import OwnerHandoffStore
from application.owner_handoff.workspace.duplicator import verify_original_unchanged
from utils.time_utils import utc_now

T = TypeVar("T")

_STEP_POLL_SECONDS = 0.05
# A short, bounded extra wait after calling terminate_fn, to give a REAL
# termination action (e.g. SIGTERM/SIGKILL of a process group) a moment to
# take effect. Only applied when a terminate_fn was actually supplied --
# there is nothing to wait for otherwise -- and deliberately small so an
# uncooperative step is still reported within a small bounded tolerance of
# the configured return_grace_period_seconds, not after its own duration.
_POST_TERMINATE_GRACE_SECONDS = 0.1


class StepDidNotFinishInGracePeriodError(RuntimeError):
    """Raised when the current atomic step does not finish within the
    configured grace period after a return was requested. ``terminate_fn``
    (if supplied) has already been invoked by the time this is raised, but
    a step that cannot actually be canceled (e.g. an in-flight remote A2A
    call with no cancellation API) may still be running in the background
    on an abandoned daemon thread -- this is reported honestly, never
    claimed as "terminated" when it wasn't confirmed."""


class ExecutionSessionStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ExecutionSession(Generic[T]):
    """A real cancellable-execution-session contract around one step
    function, backed by a single ``daemon=True`` thread (never a
    ``ThreadPoolExecutor``, and never joined at interpreter exit).

    - ``start()`` begins the step on a background daemon thread.
    - ``poll()`` is a non-blocking check for completion.
    - ``wait(timeout)`` blocks up to ``timeout`` seconds and returns
      whether it finished in time -- always bounded, never indefinite.
    - ``terminate()`` invokes the caller-supplied ``terminate_fn`` (e.g. a
      Codex process group SIGTERM/SIGKILL escalation); it does not, and
      cannot, force-stop the Python thread itself -- only cooperative
      termination of whatever real resource ``terminate_fn`` controls.
    - ``result()``/``error`` are only meaningful once ``poll()`` is True.
    """

    def __init__(
        self, step_fn: Callable[[], T], *, terminate_fn: Callable[[], None] | None = None
    ) -> None:
        self._step_fn = step_fn
        self._terminate_fn = terminate_fn
        self._done = threading.Event()
        self._result: T | None = None
        self._error: BaseException | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("ExecutionSession.start() called more than once")

        def _run() -> None:
            try:
                self._result = self._step_fn()
            except BaseException as exc:  # noqa: BLE001 -- captured, never silently dropped
                self._error = exc
            finally:
                self._done.set()

        # daemon=True: even if step_fn never returns (a truly uncooperative
        # or uncancelable step), this thread can never block process exit.
        self._thread = threading.Thread(target=_run, daemon=True, name="execution-session-step")
        self._thread.start()

    def poll(self) -> bool:
        return self._done.is_set()

    def wait(self, timeout: float) -> bool:
        return self._done.wait(timeout=timeout)

    def terminate(self) -> None:
        if self._terminate_fn is not None:
            self._terminate_fn()

    @property
    def status(self) -> ExecutionSessionStatus:
        if not self._done.is_set():
            return ExecutionSessionStatus.RUNNING
        return ExecutionSessionStatus.FAILED if self._error is not None else ExecutionSessionStatus.COMPLETED

    @property
    def error(self) -> BaseException | None:
        return self._error

    def result(self) -> T:
        if self._error is not None:
            raise self._error
        return self._result  # type: ignore[return-value]


class ReturnCoordinator:
    """Drives EXECUTING -> RETURN_REQUESTED -> READY_FOR_REVIEW -> RETURNED
    for exactly one task."""

    def __init__(
        self,
        *,
        store: OwnerHandoffStore,
        return_grace_period_seconds: float = 30.0,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if return_grace_period_seconds <= 0:
            raise ValueError("return_grace_period_seconds must be positive")
        self._store = store
        self.return_grace_period_seconds = return_grace_period_seconds
        self._clock = clock
        self._return_requested_tasks: set[str] = set()

    def is_return_requested(self, task_id: str) -> bool:
        return task_id in self._return_requested_tasks

    def request_return(self, *, task_id: str) -> None:
        """Mark this task's return as requested and stop starting new work
        for it immediately -- callers must check ``is_return_requested``
        before starting any further step. Transitions EXECUTING ->
        RETURN_REQUESTED; idempotent event_id so a repeated confirmed-return
        signal for the same episode is a safe no-op."""

        self._return_requested_tasks.add(task_id)
        self._store.apply_transition(
            task_id=task_id,
            event_id=f"return-requested:{task_id}",
            from_state=OwnerHandoffState.EXECUTING,
            to_state=OwnerHandoffState.RETURN_REQUESTED,
            reason="owner confirmed return during execution",
        )

    def run_current_step(
        self,
        step_fn: Callable[[], T],
        *,
        task_id: str,
        terminate_fn: Callable[[], None] | None = None,
    ) -> T:
        """Run ``step_fn`` (the single current atomic step) to completion.

        If return has already been requested for ``task_id`` when this is
        called, the step is still allowed to finish -- but only within
        ``return_grace_period_seconds``. If it does not finish in time,
        ``terminate_fn`` (if supplied, e.g. a Codex process group
        terminate) is invoked and this waits one short additional bounded
        interval for that to take effect before raising
        ``StepDidNotFinishInGracePeriodError`` -- the caller must not start
        another step afterward regardless of whether the underlying step
        actually stopped (a step with no real cancellation path, such as an
        in-flight A2A call, is reported as not-finished honestly, never
        claimed as terminated).
        """

        session: ExecutionSession[T] = ExecutionSession(step_fn, terminate_fn=terminate_fn)
        session.start()
        while True:
            timeout = (
                self.return_grace_period_seconds
                if self.is_return_requested(task_id)
                else _STEP_POLL_SECONDS
            )
            if session.wait(timeout):
                return session.result()
            if self.is_return_requested(task_id):
                session.terminate()
                if terminate_fn is not None:
                    # One short, bounded extra wait for the termination to
                    # actually take effect (e.g. the OS to reap a killed
                    # process) -- never an indefinite second wait, and only
                    # when there was a real action to wait on.
                    session.wait(_POST_TERMINATE_GRACE_SECONDS)
                raise StepDidNotFinishInGracePeriodError(
                    f"task {task_id!r}: current step did not finish within "
                    f"the {self.return_grace_period_seconds}s return grace period"
                )

    def finalize(
        self,
        *,
        task_id: str,
        manifest: DuplicationManifest,
        selected_task: str,
        work_completed: tuple[str, ...] = (),
        packages_installed: tuple[str, ...] = (),
        from_state: OwnerHandoffState = OwnerHandoffState.RETURN_REQUESTED,
    ) -> tuple[ResumeReport | SafetyFailureRecord, DuplicationManifest]:
        """Re-verify the original workspace, and either:

        - verification PASSED: transition to READY_FOR_REVIEW and return a
          ``ResumeReport`` (``files_created``/``files_modified_in_duplicate``
          come from ``manifest.files_created_in_duplicate``/
          ``files_modified_in_duplicate`` -- callers must run
          ``workspace.duplicator.scan_duplicate_changes`` on the manifest
          first); or
        - verification FAILED: transition to FAILED (never
          READY_FOR_REVIEW) and return a ``SafetyFailureRecord`` instead --
          never a ``ResumeReport``, and the caller must never call
          ``deliver_control`` afterward (it would fail closed anyway, since
          ``deliver_control`` only accepts a READY_FOR_REVIEW source).

        Never trusts the manifest's existing verification fields --
        ``verify_original_unchanged`` always freshly re-hashes.
        """

        verified_manifest = verify_original_unchanged(manifest)

        if not verified_manifest.verification_passed:
            self._store.apply_transition(
                task_id=task_id,
                event_id=f"verification-failed:{task_id}",
                from_state=from_state,
                to_state=OwnerHandoffState.FAILED,
                reason="original workspace verification failed; failing closed",
            )
            failure = SafetyFailureRecord(
                selected_task=selected_task,
                reason=(
                    "original workspace verification failed after execution -- "
                    "one or more original files were modified, deleted, or "
                    "renamed; review before trusting anything in the duplicate"
                ),
                original_files_modified=verified_manifest.original_files_modified,
            )
            return failure, verified_manifest

        self._store.apply_transition(
            task_id=task_id,
            event_id=f"ready-for-review:{task_id}",
            from_state=from_state,
            to_state=OwnerHandoffState.READY_FOR_REVIEW,
            reason="execution finished; original workspace re-verified",
        )

        report = ResumeReport(
            selected_task=selected_task,
            work_completed=tuple(work_completed),
            files_created=verified_manifest.files_created_in_duplicate,
            files_modified_in_duplicate=verified_manifest.files_modified_in_duplicate,
            original_files_modified=verified_manifest.original_files_modified,
            packages_installed=tuple(packages_installed),
            status="ready_for_review",
        )
        return report, verified_manifest

    def deliver_control(self, *, task_id: str) -> None:
        """READY_FOR_REVIEW -> RETURNED: hand control back to the owner.
        Nothing here merges or copies any duplicate-workspace change back
        into the original."""

        self._store.apply_transition(
            task_id=task_id,
            event_id=f"returned:{task_id}",
            from_state=OwnerHandoffState.READY_FOR_REVIEW,
            to_state=OwnerHandoffState.RETURNED,
            reason="control returned to owner",
        )
        self._return_requested_tasks.discard(task_id)
