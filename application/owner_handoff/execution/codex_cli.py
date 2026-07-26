"""Codex CLI preflight and sandboxed executor (Master Spec section 13, 14).

Hackathon limitation (documented, not hidden): parsing Codex's JSONL output
after the fact is a defense-in-depth signal, not a guarantee against a
hostile or compromised model. The sandbox, disabled command network, the
physical duplicate, this fixed prompt policy, post-hoc manifest re-hashing,
the explicit installer gate, and this module's prohibited-command detection
are independent layers — none of them, nor their combination, is a formal
proof that a sufficiently adversarial model inside the sandbox cannot cause
some effect this design did not anticipate. In particular, the
prohibited-command check below inspects events the CLI already reported
*after* a command ran (or was about to run) -- it can stop the stream and
fail the task before accepting further steps, but it does not, and cannot,
prevent the first occurrence of a command the sandbox itself allowed.

Preflight only ever runs ``codex --version`` and ``codex exec --help`` —
both are inert, read-only introspection commands that never contact any
model service or send workspace data anywhere. This module never runs a
real ``codex exec`` task in its own tests; every test uses an injected fake
process runner.

``--skip-git-repo-check`` is used deliberately: the physical duplicate this
executor operates on always excludes ``.git`` (see
``workspace/duplicator.py``), so the duplicate is never a git repository at
all, and Codex's usual "is this a trusted git repo" check would otherwise
always fail for a directory it structurally cannot recognize as one.

``--strict-config`` is required at preflight because the argv this executor
builds always includes it (a config key typo must fail loudly, not be
silently ignored).
"""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

from application.owner_handoff.domain.execution import ExecutionResult, ExecutionStatus
from application.owner_handoff.domain.work_context import redact_sensitive
from application.owner_handoff.execution.coding_executor import CodingTaskRequest
from application.owner_handoff.execution.permission import (
    PermissionNotGrantedError,
    compute_manifest_hash,
)
from application.owner_handoff.execution.policy import ExecutionPolicy, ExecutionPolicyViolation
from application.owner_handoff.state_machine import PermissionKind
from utils.time_utils import utc_now


class CodexPreflightError(RuntimeError):
    """Raised when the installed Codex CLI is missing or lacks a required
    capability. This must stop safely, never fall back to an unsafe
    invocation, and never claim the CLI universally lacks a capability —
    only that THIS installation, at preflight time, did not advertise it."""


@dataclass(frozen=True)
class CommandResult:
    """Result of one preflight (or process) command."""

    returncode: int
    stdout: str
    stderr: str = ""


class CommandRunner(Protocol):
    """Boundary for the two read-only preflight commands. Tests inject a
    fake; normal tests never invoke a real ``codex`` binary."""

    def run(self, argv: list[str]) -> CommandResult:
        ...


@dataclass(frozen=True)
class CodexCapabilities:
    version: str
    supported_flags: frozenset[str]


# Flags Phase 3 requires the installed CLI to advertise via
# `codex exec --help`. "exec" itself is confirmed simply by that command
# succeeding at all. `--strict-config` is required because the argv this
# executor builds always passes it.
REQUIRED_FLAGS = frozenset(
    {
        "exec",
        "--sandbox",
        "--cd",
        "--ephemeral",
        "--json",
        "--ignore-user-config",
        "--skip-git-repo-check",
        "--strict-config",
        "-c",
    }
)

_DETECTABLE_FLAGS = (
    "--sandbox",
    "--cd",
    "--ephemeral",
    "--json",
    "--ignore-user-config",
    "--skip-git-repo-check",
    "-c",
    "--config",
    "--strict-config",
)

_MAX_HELP_TEXT_CHARS = 20_000


class CodexPreflight:
    """Locates and verifies the configured Codex CLI at runtime.

    Runs only ``codex --version`` and ``codex exec --help`` — never
    ``codex exec`` itself, and never anything that could transmit workspace
    data to a model service. Inspects both stdout and stderr of the help
    invocation, since some CLI builds print usage/help text to stderr.
    """

    def __init__(self, *, binary: str = "codex", runner: CommandRunner) -> None:
        self.binary = binary
        self._runner = runner

    def run(self) -> CodexCapabilities:
        try:
            version_result = self._runner.run([self.binary, "--version"])
        except (OSError, FileNotFoundError) as exc:
            raise CodexPreflightError(f"codex binary not found or not runnable: {exc}") from exc
        if version_result.returncode != 0:
            raise CodexPreflightError(
                f"'{self.binary} --version' failed (exit {version_result.returncode})"
            )
        version = version_result.stdout.strip() or version_result.stderr.strip()

        try:
            help_result = self._runner.run([self.binary, "exec", "--help"])
        except (OSError, FileNotFoundError) as exc:
            raise CodexPreflightError(f"'{self.binary} exec --help' could not run: {exc}") from exc
        if help_result.returncode != 0:
            raise CodexPreflightError(
                f"'{self.binary} exec --help' failed (exit {help_result.returncode})"
            )
        # Both streams are inspected -- some CLI builds print usage/help
        # text to stderr instead of (or in addition to) stdout.
        help_text = (help_result.stdout + "\n" + help_result.stderr)[:_MAX_HELP_TEXT_CHARS]

        supported = {"exec"}
        for flag in _DETECTABLE_FLAGS:
            if flag in help_text:
                supported.add(flag)

        missing = REQUIRED_FLAGS - supported
        if missing:
            raise CodexPreflightError(
                "the installed Codex CLI does not advertise required "
                f"capabilities in 'exec --help': {sorted(missing)} "
                "(this installation only, not a claim about Codex CLI in general)"
            )

        return CodexCapabilities(version=version, supported_flags=frozenset(supported))


# Never permitted regardless of what the CLI supports.
_FORBIDDEN_FLAGS = frozenset(
    {
        "--dangerously-bypass-approvals-and-sandbox",
        "--dangerously-bypass-hook-trust",
        "--add-dir",
        "--search",
        "--ignore-rules",
    }
)

_FAILURE_EVENT_TYPES = frozenset({"error", "turn.failed", "thread.error"})
_COMPLETION_EVENT_TYPE = "turn.completed"
_SENSITIVE_EVENT_KEYS = frozenset(
    {
        "env",
        "environment",
        "api_key",
        "apikey",
        "authorization",
        "auth",
        "token",
        "secret",
        "password",
        "passwd",
        "credential",
        "credentials",
        "cookie",
        "cookies",
    }
)

# Post-hoc defense in depth only (see module docstring): if an event the CLI
# already reported describes a command containing one of these, the stream
# is terminated and the task fails before any further steps are accepted.
_PROHIBITED_COMMAND_SUBSTRINGS = (
    "git push",
    "git remote",
    "npm install",
    "npm publish",
    "yarn add",
    "yarn install",
    "pip install",
    "pip3 install",
    "curl ",
    "wget ",
    "brew install",
    "sudo ",
    "docker push",
)

MAX_RETAINED_EVENTS = 200
MAX_RETAINED_TEXT_CHARS = 2000
# Upper bound on how long a single poll_line() call may block before this
# executor re-checks its own runtime budget -- keeps the timeout check live
# even when the child process emits absolutely no output.
_POLL_INTERVAL_SECONDS = 1.0


def _contains_prohibited_command(command: str) -> bool:
    lowered = command.lower()
    return any(token in lowered for token in _PROHIBITED_COMMAND_SUBSTRINGS)


@dataclass(frozen=True)
class PollResult:
    """One ``poll_line`` outcome.

    ``line`` is the next decoded output line if one arrived within the
    requested timeout, else ``None``. ``finished`` is True once the
    process has exited and there is no more output to read -- distinct
    from "nothing yet, still running", so a caller can tell a real
    end-of-stream apart from an ordinary timeout tick.
    """

    line: str | None
    finished: bool


class CodexProcessHandle(Protocol):
    """Boundary for one running (or completed) Codex process.

    ``poll_line`` must return within (approximately) ``timeout`` seconds
    even if the child process has produced no output at all -- a blocking,
    unconditional readline is not an acceptable implementation, since it
    would make this executor's own runtime-limit check unreachable for a
    silent child.
    """

    def poll_line(self, timeout: float) -> PollResult:
        ...

    def terminate(self) -> None:
        ...

    def exit_code(self) -> int:
        ...


class CodexProcessRunner(Protocol):
    """Boundary for starting a Codex process. Tests inject a fake that
    never spawns a real subprocess."""

    def run(self, argv: list[str], *, cwd: Path, stdin_text: str) -> CodexProcessHandle:
        ...


def _sanitize_value(value: object) -> object:
    if isinstance(value, dict):
        return _sanitize_event(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str):
        return redact_sensitive(value)[:MAX_RETAINED_TEXT_CHARS]
    return value


def _sanitize_event(event: dict) -> dict:
    """Recursively strip sensitive keys and redact/bound every string,
    at any nesting depth -- not just the event's top level."""

    sanitized: dict = {}
    for key, value in event.items():
        if key.lower() in _SENSITIVE_EVENT_KEYS:
            continue
        sanitized[key] = _sanitize_value(value)
    return sanitized


def _build_prompt(goal_text: str) -> str:
    return (
        "You are working ONLY inside this duplicated workspace directory. "
        "Never reference or attempt to access any path outside it.\n\n"
        f"Make one small, reversible change toward this goal: {goal_text}\n\n"
        "Rules:\n"
        "- Do not delete any existing files.\n"
        "- Do not install any packages or dependencies yourself.\n"
        "- Do not push, publish, deploy, send messages, or upload anything.\n"
        "- If a package is required to proceed, stop and report a structured "
        "install request (the exact package name and why) instead of "
        "installing it yourself.\n"
        "- If required information is missing, stop and report what is "
        "needed instead of guessing.\n"
    )


class CodexCLIExecutor:
    """Sandboxed CodingAgentExecutor backed by the real Codex CLI.

    Every subprocess boundary (preflight ``CommandRunner``, the process
    ``CodexProcessRunner``) is injectable; normal tests use only fakes, and
    this class never invokes a real ``codex`` binary in its own test suite.

    Repair (permission enforcement): immediately before the process runner
    is ever invoked, this executor freshly hashes the live duplicate and
    requires+consumes exactly one matching CODEX_DATA permit from
    ``request.permission_gate``. If no such permit exists (never granted,
    wrong task/session/duplicate/digest, or expired), execution fails
    closed and the process runner is never called.
    """

    def __init__(
        self,
        *,
        preflight: CodexPreflight,
        runner: CodexProcessRunner,
        policy: ExecutionPolicy | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._preflight = preflight
        self._runner = runner
        self._policy = policy or ExecutionPolicy()
        self._monotonic = monotonic
        self._clock = clock
        # The live handle for whatever process `execute()` is currently
        # running, if any -- lets an external caller (ReturnCoordinator's
        # grace-period backstop) reach in and terminate it.
        self._current_handle: CodexProcessHandle | None = None

    def terminate_current(self) -> None:
        """Terminate whatever Codex process this executor is currently
        running, if any. Safe to call when nothing is running (no-op)."""

        if self._current_handle is not None:
            self._current_handle.terminate()

    def _build_argv(self, request: CodingTaskRequest) -> list[str]:
        return [
            self._preflight.binary,
            "exec",
            "--strict-config",
            "-c",
            "sandbox_workspace_write.network_access=false",
            "--sandbox",
            "workspace-write",
            "--cd",
            str(request.duplicate_path),
            "--ephemeral",
            "--json",
            "--ignore-user-config",
            "--skip-git-repo-check",
            "-",
        ]

    def execute(self, request: CodingTaskRequest) -> ExecutionResult:
        try:
            self._preflight.run()
        except CodexPreflightError as exc:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                summary=f"Codex CLI preflight failed; stopping safely: {exc}",
                detail={"reason": "preflight_failed"},
            )

        argv = self._build_argv(request)

        # Defense in depth: the fixed argv above should never contain a
        # forbidden flag, but verify anyway before ever invoking the runner.
        for item in argv:
            if item in _FORBIDDEN_FLAGS or "danger-full-access" in item:
                raise ExecutionPolicyViolation(
                    f"constructed argv unexpectedly contains a forbidden flag: {item}"
                )
        self._policy.check_argv_safe_for_subprocess(argv)
        self._policy.check_path_within_duplicate(request.duplicate_path, request.duplicate_path)

        # Require and atomically consume exactly one matching CODEX_DATA
        # permit -- computed fresh, right now, from the live duplicate --
        # before the process runner is invoked at all.
        live_digest = compute_manifest_hash(request.duplicate_path)
        try:
            request.permission_gate.consume(
                task_id=request.task_id,
                permission_kind=PermissionKind.CODEX_DATA,
                session_id=request.session_id,
                duplicate_path=request.duplicate_path,
                live_digest=live_digest,
                now=self._clock(),
                question_id=request.question_id,
            )
        except PermissionNotGrantedError as exc:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                summary=f"no valid CODEX_DATA permit; refusing to run Codex: {exc}",
                detail={"reason": "permission_not_granted"},
            )

        prompt = _build_prompt(request.goal_text)
        try:
            handle = self._runner.run(argv, cwd=request.duplicate_path, stdin_text=prompt)
        except Exception as exc:  # noqa: BLE001 -- any launch failure must become a sanitized result
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                summary=f"failed to start Codex process: {redact_sensitive(str(exc))}",
                detail={"reason": "runner_start_failed"},
            )
        self._current_handle = handle

        start = self._monotonic()
        seen_step_ids: set[str] = set()
        open_item_ids: set[str] = set()
        events: list[dict] = []
        events_truncated = False
        failure_reason: str | None = None
        completed = False
        stop_after_current_item = False

        try:
            while True:
                elapsed = self._monotonic() - start
                remaining = request.max_runtime_seconds - elapsed
                if remaining <= 0:
                    handle.terminate()
                    failure_reason = "runtime limit exceeded"
                    break

                if request.stop_requested():
                    stop_after_current_item = True

                if stop_after_current_item and not open_item_ids:
                    # Owner return confirmed and nothing is currently
                    # mid-flight -- stop right now rather than waiting for
                    # (or accepting) a new item.
                    handle.terminate()
                    failure_reason = "stopped for owner return; no new step started"
                    break

                poll = handle.poll_line(min(remaining, _POLL_INTERVAL_SECONDS))
                if poll.line is None:
                    if poll.finished:
                        break  # premature EOF: process ended with no more output
                    continue  # nothing yet within this poll window; keep waiting

                stripped = poll.line.strip()
                if not stripped:
                    if poll.finished:
                        break
                    continue

                try:
                    event = json.loads(stripped)
                except json.JSONDecodeError:
                    handle.terminate()
                    failure_reason = "malformed JSONL output"
                    break
                if not isinstance(event, dict):
                    handle.terminate()
                    failure_reason = "malformed JSONL output"
                    break

                if len(events) < MAX_RETAINED_EVENTS:
                    events.append(_sanitize_event(event))
                else:
                    events_truncated = True

                event_type = str(event.get("type", ""))

                item = event.get("item")
                if isinstance(item, dict):
                    command = item.get("command")
                    if isinstance(command, str) and _contains_prohibited_command(command):
                        handle.terminate()
                        failure_reason = (
                            f"prohibited command detected in event stream ({event_type}); "
                            "this is post-hoc detection, not prevention"
                        )
                        break
                    item_id = item.get("id")
                    if item_id and item_id not in seen_step_ids:
                        if stop_after_current_item:
                            # A genuinely new step trying to start after
                            # owner return was confirmed -- refuse it.
                            handle.terminate()
                            failure_reason = (
                                "stopped for owner return; refusing to start a new step"
                            )
                            break
                        seen_step_ids.add(item_id)
                        open_item_ids.add(item_id)
                        if len(seen_step_ids) > request.max_safe_steps:
                            handle.terminate()
                            failure_reason = "safe-step limit exceeded"
                            break
                    elif item_id and event_type.endswith("completed"):
                        open_item_ids.discard(item_id)

                if event_type in _FAILURE_EVENT_TYPES:
                    handle.terminate()
                    failure_reason = f"codex reported failure: {event_type}"
                    break

                if event_type == _COMPLETION_EVENT_TYPE:
                    # Only an explicit turn.completed counts as overall
                    # completion -- item.completed events, however many, never
                    # do on their own.
                    completed = True
                    break

                if poll.finished:
                    break

            exit_code = handle.exit_code()
        finally:
            self._current_handle = None

        detail_base = {
            "events": events,
            "events_truncated": events_truncated,
            "exit_code": exit_code,
            "steps": len(seen_step_ids),
        }

        if failure_reason is not None:
            status = (
                ExecutionStatus.CANCELED
                if failure_reason.startswith("stopped for owner return")
                else ExecutionStatus.FAILED
            )
            return ExecutionResult(
                status=status,
                summary=f"Codex execution stopped: {failure_reason}",
                detail=detail_base,
            )
        if exit_code != 0:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                summary=f"Codex CLI exited with a nonzero code ({exit_code})",
                detail=detail_base,
            )
        if not completed:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                summary="Codex execution ended without an explicit turn.completed event",
                detail=detail_base,
            )

        return ExecutionResult(
            status=ExecutionStatus.COMPLETED,
            summary="Codex execution completed",
            detail=detail_base,
        )


# ---------------------------------------------------------------------------
# Real (but never automatically invoked in tests) subprocess-backed adapters.
# ---------------------------------------------------------------------------

_MAX_CAPTURED_OUTPUT_CHARS = 200_000


class SubprocessCommandRunner:
    """Real ``CommandRunner`` for Codex preflight (``--version`` /
    ``exec --help``). ``shell=False`` always; argv is passed as a list,
    never interpolated into a shell string. Never used by this package's
    own automated tests -- only by real, human-invoked runs."""

    def __init__(self, *, timeout_seconds: float = 15.0) -> None:
        self._timeout_seconds = timeout_seconds

    def run(self, argv: list[str]) -> CommandResult:
        result = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            timeout=self._timeout_seconds,
        )
        return CommandResult(
            returncode=result.returncode,
            stdout=result.stdout[:_MAX_CAPTURED_OUTPUT_CHARS],
            stderr=result.stderr[:_MAX_CAPTURED_OUTPUT_CHARS],
        )


class _SubprocessCodexProcessHandle:
    """Real ``CodexProcessHandle``. Reads stdout on a background thread into
    a bounded queue so ``poll_line`` can honor a timeout even when the
    child emits nothing -- a plain blocking readline cannot do that.

    stderr is read on a SEPARATE background thread into a small, bounded
    tail buffer -- it is never mixed into the stdout JSONL queue, so a
    warning line the CLI prints to stderr can never be mistaken for a
    JSONL event. ``terminate()`` escalates SIGTERM -> (bounded wait) ->
    SIGKILL on POSIX/macOS if the process group does not exit in time;
    every wait here is bounded, and resources (pipes, reader threads) are
    always allowed to unwind rather than joined indefinitely.
    """

    _MAX_QUEUE_SIZE = 500
    _MAX_STDERR_CHARS = 20_000
    _TERMINATE_WAIT_SECONDS = 5.0

    def __init__(self, popen: subprocess.Popen) -> None:
        self._popen = popen
        self._queue: "queue.Queue[str | None]" = queue.Queue(maxsize=self._MAX_QUEUE_SIZE)
        self._stderr_lock = threading.Lock()
        self._stderr_tail = ""
        self._stdout_reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_reader.start()
        self._stderr_reader.start()

    def _read_stdout(self) -> None:
        try:
            assert self._popen.stdout is not None
            for line in self._popen.stdout:
                self._queue.put(line)
        except (OSError, ValueError):
            pass
        finally:
            self._queue.put(None)  # EOF sentinel

    def _read_stderr(self) -> None:
        try:
            assert self._popen.stderr is not None
            for line in self._popen.stderr:
                with self._stderr_lock:
                    self._stderr_tail = (self._stderr_tail + line)[-self._MAX_STDERR_CHARS :]
        except (OSError, ValueError):
            pass

    @property
    def stderr_tail(self) -> str:
        """Bounded, sanitized-on-read tail of stderr -- diagnostics only,
        never parsed as JSONL."""

        with self._stderr_lock:
            return redact_sensitive(self._stderr_tail)

    def poll_line(self, timeout: float) -> PollResult:
        try:
            item = self._queue.get(timeout=max(timeout, 0.0))
        except queue.Empty:
            return PollResult(line=None, finished=False)
        if item is None:
            return PollResult(line=None, finished=True)
        return PollResult(line=item, finished=False)

    def _signal_group(self, sig: int, *, fallback: Callable[[], None]) -> None:
        try:
            if os.name != "nt" and hasattr(os, "killpg"):
                os.killpg(os.getpgid(self._popen.pid), sig)
            else:
                fallback()
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def terminate(self) -> None:
        """SIGTERM the process group, wait a bounded amount of time, then
        escalate to SIGKILL if it hasn't exited -- never an indefinite
        second wait."""

        self._signal_group(getattr(signal, "SIGTERM", 15), fallback=self._popen.terminate)
        try:
            self._popen.wait(timeout=self._TERMINATE_WAIT_SECONDS)
            return
        except subprocess.TimeoutExpired:
            pass

        self._signal_group(getattr(signal, "SIGKILL", 9), fallback=self._popen.kill)
        try:
            self._popen.wait(timeout=self._TERMINATE_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            pass  # bounded: give up rather than wait indefinitely a third time

    def exit_code(self) -> int:
        code = self._popen.poll()
        if code is not None:
            return code
        try:
            return self._popen.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.terminate()
            try:
                return self._popen.wait(timeout=5)
            except subprocess.TimeoutExpired:
                return -1  # bounded: never wait indefinitely a second time


class SubprocessCodexProcessRunner:
    """Real ``CodexProcessRunner``: ``shell=False``, argv as a list, the
    prompt delivered over stdin (never argv), started in its own process
    group on POSIX so ``terminate()`` can safely stop the whole group.
    stdout and stderr are captured on separate pipes/threads -- stderr is
    never merged into the JSONL stdout stream. Never used by this
    package's own automated tests."""

    def run(self, argv: list[str], *, cwd: Path, stdin_text: str) -> CodexProcessHandle:
        popen = subprocess.Popen(
            argv,
            cwd=str(cwd),
            shell=False,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=(os.name != "nt"),
        )
        assert popen.stdin is not None
        popen.stdin.write(stdin_text)
        popen.stdin.close()
        return _SubprocessCodexProcessHandle(popen)
