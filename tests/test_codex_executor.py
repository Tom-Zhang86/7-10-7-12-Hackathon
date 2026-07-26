import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from application.owner_handoff.domain.execution import ExecutionStatus
from application.owner_handoff.execution.coding_executor import CodingTaskRequest
from application.owner_handoff.execution.codex_cli import (
    CodexCLIExecutor,
    CodexPreflight,
    CodexPreflightError,
    CommandResult,
    PollResult,
    SubprocessCommandRunner,
)
from application.owner_handoff.execution.permission import PermissionAuthorization, PermissionGate
from application.owner_handoff.execution.policy import ExecutionPolicyViolation
from application.owner_handoff.state_machine import PermissionKind

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)

_HELP_TEXT = (
    "codex exec [OPTIONS]\n"
    "--sandbox <MODE>\n"
    "--cd <DIR>\n"
    "--ephemeral\n"
    "--json\n"
    "--ignore-user-config\n"
    "--skip-git-repo-check\n"
    "-c, --config <KEY=VALUE>\n"
    "--strict-config\n"
)


class FakeCommandRunner:
    def __init__(self, *, version_result: CommandResult, help_result: CommandResult) -> None:
        self.version_result = version_result
        self.help_result = help_result
        self.calls: list[list[str]] = []

    def run(self, argv: list[str]) -> CommandResult:
        self.calls.append(argv)
        if argv[1:] == ["--version"]:
            return self.version_result
        return self.help_result


def _working_preflight() -> tuple[CodexPreflight, FakeCommandRunner]:
    runner = FakeCommandRunner(
        version_result=CommandResult(0, "codex-cli 0.134.0\n"),
        help_result=CommandResult(0, _HELP_TEXT),
    )
    return CodexPreflight(binary="codex", runner=runner), runner


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeCodexProcessHandle:
    """Poll-based fake: ``poll_line`` never blocks -- it returns within the
    requested timeout even if there is no more output, which is exactly
    the property the real (thread+queue backed) adapter must have."""

    def __init__(
        self,
        lines: list[str],
        *,
        exit_code: int = 0,
        clock: _FakeClock | None = None,
        time_per_line: float = 0.0,
        never_finish: bool = False,
    ) -> None:
        self._lines = list(lines)
        self._index = 0
        self._exit_code = exit_code
        self._terminated = False
        self._clock = clock
        self._time_per_line = time_per_line
        self._never_finish = never_finish

    def poll_line(self, timeout: float) -> PollResult:
        if self._terminated:
            return PollResult(line=None, finished=True)
        if self._index >= len(self._lines):
            if self._clock is not None:
                self._clock.advance(timeout)
            if self._never_finish:
                return PollResult(line=None, finished=False)
            return PollResult(line=None, finished=True)
        if self._clock is not None:
            self._clock.advance(self._time_per_line)
        line = self._lines[self._index]
        self._index += 1
        return PollResult(line=line, finished=False)

    def terminate(self) -> None:
        self._terminated = True

    def exit_code(self) -> int:
        return -1 if self._terminated else self._exit_code

    @property
    def terminated(self) -> bool:
        return self._terminated


class FakeCodexProcessRunner:
    def __init__(self, handle: FakeCodexProcessHandle) -> None:
        self._handle = handle
        self.calls: list[dict] = []

    def run(self, argv, *, cwd, stdin_text):
        self.calls.append({"argv": argv, "cwd": cwd, "stdin_text": stdin_text})
        return self._handle


def _event(event_type: str, *, item: dict | None = None, turn: dict | None = None, **extra) -> str:
    payload: dict = {"type": event_type}
    if item is not None:
        payload["item"] = item
    if turn is not None:
        payload["turn"] = turn
    payload.update(extra)
    return json.dumps(payload)


def _item_completed(item_id: str, **item_extra) -> str:
    return _event("item.completed", item={"id": item_id, "type": "command_execution", **item_extra})


def _success_lines(n: int = 2) -> list[str]:
    lines = [_event("thread.started")]
    for i in range(n):
        lines.append(_item_completed(f"item-{i}"))
    lines.append(_event("turn.completed", turn={"id": "turn-1"}))
    return lines


def _grant_permit(
    gate: PermissionGate,
    *,
    task_id: str,
    session_id: str,
    duplicate_path: Path,
    question_id: str,
    live_digest: str,
    kind: PermissionKind = PermissionKind.CODEX_DATA,
) -> None:
    authorization = PermissionAuthorization(
        permission_kind=kind,
        task_id=task_id,
        question_id=question_id,
        session_id=session_id,
        duplicate_path=str(duplicate_path),
        duplicate_digest=live_digest,
        expires_at=NOW.replace(year=NOW.year + 1),
        granted_at=NOW,
    )
    gate._authorizations[(task_id, kind)] = authorization  # test-only direct grant


def _request(duplicate: Path, *, gate: PermissionGate | None = None, **overrides) -> CodingTaskRequest:
    from application.owner_handoff.execution.permission import compute_manifest_hash

    gate = gate if gate is not None else PermissionGate()
    if "permission_gate" not in overrides:
        _grant_permit(
            gate,
            task_id="task-1",
            session_id="session-1",
            duplicate_path=duplicate,
            question_id="permission-1",
            live_digest=compute_manifest_hash(duplicate),
        )
    defaults = dict(
        goal_text="Fix the failing test in the renderer",
        duplicate_path=duplicate,
        max_runtime_seconds=300.0,
        max_safe_steps=20,
        task_id="task-1",
        session_id="session-1",
        question_id="permission-1",
        permission_gate=gate,
    )
    defaults.update(overrides)
    return CodingTaskRequest(**defaults)


class CodexPreflightNormalPathTest(unittest.TestCase):
    def test_preflight_only_runs_version_and_help(self) -> None:
        preflight, runner = _working_preflight()
        capabilities = preflight.run()
        self.assertEqual(capabilities.version, "codex-cli 0.134.0")
        self.assertIn("--sandbox", capabilities.supported_flags)
        self.assertEqual(
            [call[1:] for call in runner.calls],
            [["--version"], ["exec", "--help"]],
        )

    def test_help_text_inspected_on_stderr_too(self) -> None:
        runner = FakeCommandRunner(
            version_result=CommandResult(0, "codex-cli 0.134.0\n"),
            help_result=CommandResult(0, "", _HELP_TEXT),
        )
        preflight = CodexPreflight(binary="codex", runner=runner)
        capabilities = preflight.run()
        self.assertIn("--strict-config", capabilities.supported_flags)


class CodexPreflightRejectionTest(unittest.TestCase):
    def test_missing_binary_fails_closed(self) -> None:
        class RaisingRunner:
            def run(self, argv):
                raise FileNotFoundError("no such file")

        preflight = CodexPreflight(binary="codex", runner=RaisingRunner())
        with self.assertRaises(CodexPreflightError):
            preflight.run()

    def test_missing_required_flag_fails_closed(self) -> None:
        runner = FakeCommandRunner(
            version_result=CommandResult(0, "codex-cli 0.100.0\n"),
            help_result=CommandResult(0, "codex exec [OPTIONS]\n--json\n"),  # missing most flags
        )
        preflight = CodexPreflight(binary="codex", runner=runner)
        with self.assertRaises(CodexPreflightError):
            preflight.run()

    def test_missing_strict_config_fails_closed(self) -> None:
        text = _HELP_TEXT.replace("--strict-config\n", "")
        runner = FakeCommandRunner(
            version_result=CommandResult(0, "codex-cli 0.134.0\n"),
            help_result=CommandResult(0, text),
        )
        preflight = CodexPreflight(binary="codex", runner=runner)
        with self.assertRaises(CodexPreflightError):
            preflight.run()

    def test_nonzero_version_exit_fails_closed(self) -> None:
        runner = FakeCommandRunner(
            version_result=CommandResult(1, "", "not found"),
            help_result=CommandResult(0, _HELP_TEXT),
        )
        preflight = CodexPreflight(binary="codex", runner=runner)
        with self.assertRaises(CodexPreflightError):
            preflight.run()


class CodexCLIExecutorArgvTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.duplicate = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_safe_argv_and_stdin_prompt_and_cwd(self) -> None:
        preflight, _ = _working_preflight()
        handle = FakeCodexProcessHandle(_success_lines())
        process_runner = FakeCodexProcessRunner(handle)
        executor = CodexCLIExecutor(preflight=preflight, runner=process_runner)

        result = executor.execute(_request(self.duplicate))

        self.assertEqual(result.status, ExecutionStatus.COMPLETED)
        call = process_runner.calls[0]
        argv = call["argv"]
        self.assertNotIsInstance(argv, str)
        self.assertEqual(call["cwd"], self.duplicate)
        self.assertIn("Fix the failing test", call["stdin_text"])
        self.assertNotIn("Fix the failing test", argv)  # prompt is stdin, never argv
        self.assertIn("--sandbox", argv)
        self.assertIn("workspace-write", argv)
        self.assertIn("--cd", argv)
        self.assertIn(str(self.duplicate), argv)
        self.assertIn("--ephemeral", argv)
        self.assertIn("--json", argv)
        self.assertIn("--ignore-user-config", argv)
        self.assertIn("--skip-git-repo-check", argv)
        self.assertIn("--strict-config", argv)

    def test_no_dangerous_flags_present(self) -> None:
        preflight, _ = _working_preflight()
        handle = FakeCodexProcessHandle(_success_lines())
        executor = CodexCLIExecutor(preflight=preflight, runner=FakeCodexProcessRunner(handle))
        executor.execute(_request(self.duplicate))
        argv = executor._build_argv(_request(self.duplicate))
        for forbidden in (
            "--dangerously-bypass-approvals-and-sandbox",
            "--dangerously-bypass-hook-trust",
            "--add-dir",
            "--search",
            "--ignore-rules",
        ):
            self.assertNotIn(forbidden, argv)
        self.assertFalse(any("danger-full-access" in item for item in argv))

    def test_network_access_explicitly_false(self) -> None:
        preflight, _ = _working_preflight()
        executor = CodexCLIExecutor(preflight=preflight, runner=FakeCodexProcessRunner(
            FakeCodexProcessHandle(_success_lines())
        ))
        argv = executor._build_argv(_request(self.duplicate))
        self.assertIn("sandbox_workspace_write.network_access=false", argv)

    def test_no_original_path_passed_to_codex(self) -> None:
        preflight, _ = _working_preflight()
        executor = CodexCLIExecutor(preflight=preflight, runner=FakeCodexProcessRunner(
            FakeCodexProcessHandle(_success_lines())
        ))
        argv = executor._build_argv(_request(self.duplicate))
        self.assertNotIn("original", " ".join(argv).lower())


class CodexCLIExecutorPreflightIntegrationTest(unittest.TestCase):
    def test_missing_binary_never_invokes_process_runner(self) -> None:
        class RaisingRunner:
            def run(self, argv):
                raise FileNotFoundError("no such file")

        preflight = CodexPreflight(binary="codex", runner=RaisingRunner())
        process_runner = FakeCodexProcessRunner(FakeCodexProcessHandle(_success_lines()))
        executor = CodexCLIExecutor(preflight=preflight, runner=process_runner)

        with tempfile.TemporaryDirectory() as tmp:
            result = executor.execute(_request(Path(tmp)))

        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertEqual(len(process_runner.calls), 0)  # Codex subprocess call count remains zero


class CodexCLIExecutorPermissionEnforcementTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.duplicate = Path(self.temp_dir.name)
        self.preflight, _ = _working_preflight()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_no_permit_fails_closed_before_runner_called(self) -> None:
        handle = FakeCodexProcessHandle(_success_lines())
        process_runner = FakeCodexProcessRunner(handle)
        executor = CodexCLIExecutor(preflight=self.preflight, runner=process_runner)
        gate = PermissionGate()  # never granted anything
        request = CodingTaskRequest(
            goal_text="Fix the failing test",
            duplicate_path=self.duplicate,
            max_runtime_seconds=300.0,
            max_safe_steps=20,
            task_id="task-1",
            session_id="session-1",
            question_id="permission-1",
            permission_gate=gate,
        )
        result = executor.execute(request)
        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertIn("permit", result.summary.lower())
        self.assertEqual(len(process_runner.calls), 0)

    def test_permit_consumed_exactly_once(self) -> None:
        handle = FakeCodexProcessHandle(_success_lines())
        process_runner = FakeCodexProcessRunner(handle)
        executor = CodexCLIExecutor(preflight=self.preflight, runner=process_runner)
        request = _request(self.duplicate)

        result = executor.execute(request)
        self.assertEqual(result.status, ExecutionStatus.COMPLETED)
        self.assertEqual(len(process_runner.calls), 1)

        # The permit was consumed by the first execute() -- a second
        # attempt with the same request must fail closed and must not
        # invoke the runner again.
        second_handle = FakeCodexProcessHandle(_success_lines())
        second_runner = FakeCodexProcessRunner(second_handle)
        second_executor = CodexCLIExecutor(preflight=self.preflight, runner=second_runner)
        second_result = second_executor.execute(request)
        self.assertEqual(second_result.status, ExecutionStatus.FAILED)
        self.assertEqual(len(second_runner.calls), 0)

    def test_duplicate_tampered_after_grant_fails_closed(self) -> None:
        gate = PermissionGate()
        from application.owner_handoff.execution.permission import compute_manifest_hash

        _grant_permit(
            gate, task_id="task-1", session_id="session-1", duplicate_path=self.duplicate,
            question_id="permission-1", live_digest=compute_manifest_hash(self.duplicate),
        )
        (self.duplicate / "new_file.txt").write_text("changed after grant\n")
        handle = FakeCodexProcessHandle(_success_lines())
        process_runner = FakeCodexProcessRunner(handle)
        executor = CodexCLIExecutor(preflight=self.preflight, runner=process_runner)
        request = _request(self.duplicate, permission_gate=gate)
        result = executor.execute(request)
        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertEqual(len(process_runner.calls), 0)


class CodexCLIExecutorJSONLTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.duplicate = Path(self.temp_dir.name)
        self.preflight, _ = _working_preflight()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_success_stream_completes(self) -> None:
        handle = FakeCodexProcessHandle(_success_lines())
        executor = CodexCLIExecutor(preflight=self.preflight, runner=FakeCodexProcessRunner(handle))
        result = executor.execute(_request(self.duplicate))
        self.assertEqual(result.status, ExecutionStatus.COMPLETED)

    def test_failure_event_produces_failed(self) -> None:
        lines = [_event("thread.started"), _event("error", message="boom")]
        handle = FakeCodexProcessHandle(lines)
        executor = CodexCLIExecutor(preflight=self.preflight, runner=FakeCodexProcessRunner(handle))
        result = executor.execute(_request(self.duplicate))
        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertTrue(handle.terminated)

    def test_turn_failed_produces_failed(self) -> None:
        lines = [_event("thread.started"), _event("turn.failed", turn={"id": "turn-1"})]
        handle = FakeCodexProcessHandle(lines)
        executor = CodexCLIExecutor(preflight=self.preflight, runner=FakeCodexProcessRunner(handle))
        result = executor.execute(_request(self.duplicate))
        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertTrue(handle.terminated)

    def test_malformed_json_produces_failed(self) -> None:
        lines = [_event("thread.started"), "{not valid json"]
        handle = FakeCodexProcessHandle(lines)
        executor = CodexCLIExecutor(preflight=self.preflight, runner=FakeCodexProcessRunner(handle))
        result = executor.execute(_request(self.duplicate))
        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertIn("malformed", result.summary.lower())

    def test_nonzero_exit_produces_failed(self) -> None:
        handle = FakeCodexProcessHandle(_success_lines(), exit_code=1)
        executor = CodexCLIExecutor(preflight=self.preflight, runner=FakeCodexProcessRunner(handle))
        result = executor.execute(_request(self.duplicate))
        self.assertEqual(result.status, ExecutionStatus.FAILED)

    def test_item_completed_without_turn_completed_produces_failed(self) -> None:
        lines = [_event("thread.started"), _item_completed("item-0"), _item_completed("item-1")]
        handle = FakeCodexProcessHandle(lines)
        executor = CodexCLIExecutor(preflight=self.preflight, runner=FakeCodexProcessRunner(handle))
        result = executor.execute(_request(self.duplicate))
        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertIn("turn.completed", result.summary)

    def test_premature_eof_produces_failed(self) -> None:
        handle = FakeCodexProcessHandle([_event("thread.started")])
        executor = CodexCLIExecutor(preflight=self.preflight, runner=FakeCodexProcessRunner(handle))
        result = executor.execute(_request(self.duplicate))
        self.assertEqual(result.status, ExecutionStatus.FAILED)

    def test_timeout_terminates_and_fails_with_slow_output(self) -> None:
        clock = _FakeClock()
        lines = [_item_completed(f"item-{i}") for i in range(5)]
        handle = FakeCodexProcessHandle(lines, clock=clock, time_per_line=200.0)
        executor = CodexCLIExecutor(
            preflight=self.preflight, runner=FakeCodexProcessRunner(handle), monotonic=clock
        )
        result = executor.execute(_request(self.duplicate, max_runtime_seconds=300.0))
        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertIn("runtime", result.summary.lower())
        self.assertTrue(handle.terminated)

    def test_timeout_terminates_even_with_zero_output(self) -> None:
        """A child that emits nothing at all must still be caught by the
        runtime limit -- a blocking iter_lines()-style read would make this
        unreachable; poll_line() must return promptly on every tick."""

        clock = _FakeClock()
        handle = FakeCodexProcessHandle([], clock=clock, never_finish=True)
        executor = CodexCLIExecutor(
            preflight=self.preflight, runner=FakeCodexProcessRunner(handle), monotonic=clock
        )
        result = executor.execute(_request(self.duplicate, max_runtime_seconds=5.0))
        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertIn("runtime", result.summary.lower())
        self.assertTrue(handle.terminated)

    def test_step_cap_terminates_and_fails_with_25_nested_events(self) -> None:
        lines = [_item_completed(f"item-{i}") for i in range(25)]
        handle = FakeCodexProcessHandle(lines)
        executor = CodexCLIExecutor(preflight=self.preflight, runner=FakeCodexProcessRunner(handle))
        result = executor.execute(_request(self.duplicate, max_safe_steps=20))
        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertIn("step", result.summary.lower())
        self.assertTrue(handle.terminated)
        self.assertEqual(result.detail["steps"], 21)

    def test_sanitized_logs_never_contain_secrets(self) -> None:
        lines = [
            _item_completed("i1", message="token=SUPERSECRET123abc"),
            _event("turn.completed", turn={"id": "t1"}),
        ]
        handle = FakeCodexProcessHandle(lines)
        executor = CodexCLIExecutor(preflight=self.preflight, runner=FakeCodexProcessRunner(handle))
        result = executor.execute(_request(self.duplicate))
        self.assertNotIn("SUPERSECRET123abc", str(result.detail))

    def test_sensitive_keys_stripped_recursively(self) -> None:
        lines = [
            _item_completed("i1", nested={"authorization": "Bearer abc", "env": {"API_KEY": "abc"}}),
            _event("turn.completed", turn={"id": "t1"}),
        ]
        handle = FakeCodexProcessHandle(lines)
        executor = CodexCLIExecutor(preflight=self.preflight, runner=FakeCodexProcessRunner(handle))
        result = executor.execute(_request(self.duplicate))
        detail_str = str(result.detail)
        self.assertNotIn("authorization", detail_str.lower())
        self.assertNotIn("api_key", detail_str.lower())
        self.assertNotIn("abc", detail_str)

    def test_package_request_event_is_preserved_for_reporting(self) -> None:
        lines = [
            _item_completed("i1", structured_install_request={"package": "requests"}),
            _event("turn.completed", turn={"id": "t1"}),
        ]
        handle = FakeCodexProcessHandle(lines)
        executor = CodexCLIExecutor(preflight=self.preflight, runner=FakeCodexProcessRunner(handle))
        result = executor.execute(_request(self.duplicate))
        self.assertIn("requests", str(result.detail))

    def test_no_real_codex_process_in_tests(self) -> None:
        handle = FakeCodexProcessHandle(_success_lines())
        runner = FakeCodexProcessRunner(handle)
        executor = CodexCLIExecutor(preflight=self.preflight, runner=runner)
        self.assertIs(executor._runner, runner)


class CodexCLIExecutorProhibitedCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.duplicate = Path(self.temp_dir.name)
        self.preflight, _ = _working_preflight()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _run_with_command(self, command: str):
        lines = [
            _item_completed("i1", command=command),
            _event("turn.completed", turn={"id": "t1"}),
        ]
        handle = FakeCodexProcessHandle(lines)
        executor = CodexCLIExecutor(preflight=self.preflight, runner=FakeCodexProcessRunner(handle))
        result = executor.execute(_request(self.duplicate))
        return result, handle

    def test_git_push_command_fails(self) -> None:
        result, handle = self._run_with_command("git push origin main")
        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertTrue(handle.terminated)

    def test_npm_install_command_fails(self) -> None:
        result, handle = self._run_with_command("npm install left-pad")
        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertTrue(handle.terminated)

    def test_curl_command_fails(self) -> None:
        result, handle = self._run_with_command("curl https://example.com")
        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertTrue(handle.terminated)

    def test_safe_command_does_not_fail(self) -> None:
        result, handle = self._run_with_command("python -m pytest")
        self.assertEqual(result.status, ExecutionStatus.COMPLETED)


class CodexCLIExecutorProhibitedActionTest(unittest.TestCase):
    def test_forbidden_flag_in_argv_raises_before_runner_called(self) -> None:
        preflight, _ = _working_preflight()
        handle = FakeCodexProcessHandle(_success_lines())
        process_runner = FakeCodexProcessRunner(handle)
        executor = CodexCLIExecutor(preflight=preflight, runner=process_runner)

        original_build = executor._build_argv
        executor._build_argv = lambda request: original_build(request) + [
            "--dangerously-bypass-approvals-and-sandbox"
        ]

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ExecutionPolicyViolation):
                executor.execute(_request(Path(tmp)))
        self.assertEqual(len(process_runner.calls), 0)


class SubprocessAdapterSmokeTest(unittest.TestCase):
    """Proves the real subprocess adapters actually shell out correctly
    (argv list, shell=False) without ever invoking Codex, the network, or
    a package installer -- uses the current Python interpreter's own
    --version as an inert, always-available real command."""

    def test_subprocess_command_runner_executes_a_real_safe_command(self) -> None:
        runner = SubprocessCommandRunner()
        result = runner.run([sys.executable, "--version"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("Python", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
