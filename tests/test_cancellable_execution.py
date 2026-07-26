import json
import tempfile
import unittest
from pathlib import Path

from application.owner_handoff.domain.execution import ExecutionStatus
from application.owner_handoff.execution.coding_executor import (
    CodingTaskRequest,
    MultiStepFakeCodingExecutor,
)
from application.owner_handoff.execution.codex_cli import (
    CodexCLIExecutor,
    CodexPreflight,
    CommandResult,
    PollResult,
)
from application.owner_handoff.execution.permission import PermissionAuthorization, PermissionGate
from application.owner_handoff.return_coordinator import ReturnCoordinator
from application.owner_handoff.state_machine import OwnerHandoffState, PermissionKind
from application.owner_handoff.store import OwnerHandoffStore
from datetime import datetime, timezone

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
S = OwnerHandoffState

_HELP_TEXT = (
    "codex exec [OPTIONS]\n--sandbox <MODE>\n--cd <DIR>\n--ephemeral\n--json\n"
    "--ignore-user-config\n--skip-git-repo-check\n-c, --config <KEY=VALUE>\n--strict-config\n"
)


def _working_preflight():
    class _Runner:
        def run(self, argv):
            if argv[1:] == ["--version"]:
                return CommandResult(0, "codex-cli 0.1.0\n")
            return CommandResult(0, _HELP_TEXT)

    return CodexPreflight(binary="codex", runner=_Runner())


def _event(event_type, *, item=None, **extra):
    payload = {"type": event_type}
    if item is not None:
        payload["item"] = item
    payload.update(extra)
    return json.dumps(payload)


class MultiStepFakeCodingExecutorTest(unittest.TestCase):
    def _request(self, duplicate, stop_requested):
        gate = PermissionGate()
        return CodingTaskRequest(
            goal_text="do the thing", duplicate_path=duplicate, max_runtime_seconds=10.0,
            max_safe_steps=20, task_id="t1", session_id="s1", question_id="q1",
            permission_gate=gate, stop_requested=stop_requested,
        )

    def test_runs_all_steps_when_never_asked_to_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            executor = MultiStepFakeCodingExecutor(step_count=5)
            result = executor.execute(self._request(Path(tmp), lambda: False))
            self.assertEqual(result.status, ExecutionStatus.COMPLETED)
            self.assertEqual(executor.completed_steps, [1, 2, 3, 4, 5])

    def test_return_during_step_n_finishes_n_but_never_starts_n_plus_1(self) -> None:
        """The core repair-3 guarantee for the fake: stop is only ever
        checked BEFORE a step starts, never mid-step, and a step that has
        already started always finishes."""

        with tempfile.TemporaryDirectory() as tmp:
            stop = {"flag": False}

            def on_step(step: int) -> None:
                if step == 3:
                    stop["flag"] = True  # flips only after step 3 has begun

            executor = MultiStepFakeCodingExecutor(step_count=5, on_step=on_step)
            result = executor.execute(
                self._request(Path(tmp), lambda: stop["flag"])
            )
            self.assertEqual(result.status, ExecutionStatus.CANCELED)
            self.assertEqual(executor.completed_steps, [1, 2, 3])
            self.assertEqual(executor.started_steps, [1, 2, 3])
            self.assertNotIn(4, executor.started_steps)

    def test_wired_through_return_coordinator_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = OwnerHandoffStore(Path(tmp) / "db.sqlite3")
            store.create_task("task-1")
            for event_id, frm, to in (
                ("e1", S.OBSERVING, S.LEFT_CANDIDATE), ("e2", S.LEFT_CANDIDATE, S.OWNER_LEFT_CONFIRMED),
                ("e3", S.OWNER_LEFT_CONFIRMED, S.QUESTION_PENDING), ("e4", S.QUESTION_PENDING, S.AUTHORIZED),
                ("e5", S.AUTHORIZED, S.EXECUTING),
            ):
                store.apply_transition(task_id="task-1", event_id=event_id, from_state=frm, to_state=to, reason="setup")

            coordinator = ReturnCoordinator(store=store, return_grace_period_seconds=2.0)
            executor = MultiStepFakeCodingExecutor(step_count=5)

            def on_step(step: int) -> None:
                if step == 2:
                    coordinator.request_return(task_id="task-1")

            executor = MultiStepFakeCodingExecutor(step_count=5, on_step=on_step)
            request = self._request(
                Path(tmp), lambda: coordinator.is_return_requested("task-1")
            )
            result = coordinator.run_current_step(
                lambda: executor.execute(request), task_id="task-1"
            )
            self.assertEqual(result.status, ExecutionStatus.CANCELED)
            self.assertEqual(executor.completed_steps, [1, 2])
            self.assertEqual(store.get_task("task-1").state, S.RETURN_REQUESTED)
            store.close()


class FakeCodexHandle:
    def __init__(self, lines):
        self._lines = list(lines)
        self._index = 0
        self.terminated = False

    def poll_line(self, timeout):
        if self.terminated or self._index >= len(self._lines):
            return PollResult(line=None, finished=True)
        line = self._lines[self._index]
        self._index += 1
        return PollResult(line=line, finished=False)

    def terminate(self):
        self.terminated = True

    def exit_code(self):
        return -1 if self.terminated else 0


class FakeCodexRunner:
    def __init__(self, handle):
        self._handle = handle
        self.calls = []

    def run(self, argv, *, cwd, stdin_text):
        self.calls.append({"argv": argv, "cwd": cwd})
        return self._handle


def _grant(gate, duplicate, *, task_id="task-1", question_id="q1"):
    from application.owner_handoff.execution.permission import compute_manifest_hash

    gate._authorizations[(task_id, PermissionKind.CODEX_DATA)] = PermissionAuthorization(
        permission_kind=PermissionKind.CODEX_DATA, task_id=task_id, question_id=question_id,
        session_id="s1", duplicate_path=str(duplicate),
        duplicate_digest=compute_manifest_hash(duplicate),
        expires_at=NOW.replace(year=2030), granted_at=NOW,
    )


def _request(duplicate, stop_requested=lambda: False, gate=None):
    if gate is None:
        gate = PermissionGate()
        _grant(gate, duplicate)
    return CodingTaskRequest(
        goal_text="fix it", duplicate_path=duplicate, max_runtime_seconds=300.0,
        max_safe_steps=20, task_id="task-1", session_id="s1", question_id="q1",
        permission_gate=gate, stop_requested=stop_requested,
    )


class CodexCLIExecutorCooperativeStopTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.duplicate = Path(self.temp_dir.name)
        self.preflight = _working_preflight()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_stop_requested_before_any_item_starts_terminates_immediately(self) -> None:
        lines = [_event("thread.started")]
        handle = FakeCodexHandle(lines)
        runner = FakeCodexRunner(handle)
        executor = CodexCLIExecutor(preflight=self.preflight, runner=runner)
        result = executor.execute(_request(self.duplicate, stop_requested=lambda: True))
        self.assertEqual(result.status, ExecutionStatus.CANCELED)
        self.assertTrue(handle.terminated)

    def test_open_item_is_allowed_to_finish_then_stops_before_next_item(self) -> None:
        lines = [
            _event("item.started", item={"id": "item-0", "type": "command_execution"}),
            _event("item.completed", item={"id": "item-0", "type": "command_execution"}),
            _event("item.started", item={"id": "item-1", "type": "command_execution"}),
        ]
        stop = {"flag": False}

        class _StopAfterFirstCompleted(FakeCodexHandle):
            def poll_line(self, timeout):
                result = super().poll_line(timeout)
                if result.line and "item.completed" in result.line:
                    stop["flag"] = True
                return result

        handle = _StopAfterFirstCompleted(lines)
        runner = FakeCodexRunner(handle)
        executor = CodexCLIExecutor(preflight=self.preflight, runner=runner)
        result = executor.execute(_request(self.duplicate, stop_requested=lambda: stop["flag"]))
        self.assertEqual(result.status, ExecutionStatus.CANCELED)
        self.assertIn("no new step started", result.summary)
        self.assertTrue(handle.terminated)

    def test_runner_start_failure_is_sanitized_and_permit_stays_consumed(self) -> None:
        """Repair 8: a runner-start exception must become a sanitized
        ExecutionResult (never a raw traceback / raw secret-bearing string
        propagated), and the CODEX_DATA permit it already consumed must
        NOT be un-consumed -- a retry requires a fresh YES."""

        class _RaisingRunner:
            def __init__(self):
                self.calls = 0

            def run(self, argv, *, cwd, stdin_text):
                self.calls += 1
                raise OSError("launch failed: token=SUPERSECRET123abc")

        gate = PermissionGate()
        _grant(gate, self.duplicate)
        runner = _RaisingRunner()
        executor = CodexCLIExecutor(preflight=self.preflight, runner=runner)

        result = executor.execute(_request(self.duplicate, gate=gate))
        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertNotIn("SUPERSECRET123abc", result.summary)
        self.assertNotIn("SUPERSECRET123abc", str(result.detail))

        # The permit was consumed before the (failed) launch attempt --
        # a second attempt must fail closed (a FAILED ExecutionResult
        # reporting "no valid permit", the executor's normal fail-closed
        # reporting path) rather than silently reusing it or re-invoking
        # the runner.
        second_result = executor.execute(_request(self.duplicate, gate=gate))
        self.assertEqual(second_result.status, ExecutionStatus.FAILED)
        self.assertIn("permit", second_result.summary.lower())
        self.assertEqual(runner.calls, 1)  # never even attempted the second real launch

    def test_terminate_current_reaches_the_live_handle(self) -> None:
        """Proves the executor retains a reachable, live process handle
        while executing -- an external caller (the ReturnCoordinator's
        grace-period backstop) can call ``terminate_current()`` and have
        it actually stop the in-flight process."""

        import threading

        class _BlockingHandle(FakeCodexHandle):
            def poll_line(self, timeout):
                # Never produces a line and never finishes on its own --
                # only an external terminate() ends it.
                if self.terminated:
                    return PollResult(line=None, finished=True)
                return PollResult(line=None, finished=False)

        handle = _BlockingHandle([])
        runner = FakeCodexRunner(handle)
        executor = CodexCLIExecutor(preflight=self.preflight, runner=runner)

        self.assertIsNone(executor._current_handle)
        result_holder = {}

        def _run():
            result_holder["result"] = executor.execute(
                _request(self.duplicate, stop_requested=lambda: False)
            )

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        try:
            # Give the executor a moment to reach the point where it has
            # stored the live handle (bounded polling, no fixed sleep).
            for _ in range(200):
                if executor._current_handle is not None:
                    break
                threading.Event().wait(0.01)
            self.assertIsNotNone(executor._current_handle)
            executor.terminate_current()
        finally:
            thread.join(timeout=5.0)
        self.assertTrue(handle.terminated)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result_holder["result"].status, ExecutionStatus.FAILED)


if __name__ == "__main__":
    unittest.main()
