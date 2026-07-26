#!/usr/bin/env python3
"""AI Desk V2 -- deterministic, hardware-free Terminal demo (Master Spec,
Phase 4).

Default invocation (no hardware, no OCR, no network, no OpenAI keys, no
Codex, no agent-skeleton required):

    python3 run_owner_handoff_demo.py \\
      --simulate \\
      --workspace demo_workspace/sample_project \\
      --executor fake

``--workspace`` is always mandatory -- this script never silently falls
back to the current working directory, the repo root, or the home
directory.

The wearable simulator only ever reports (question_id, letters) back to
this script -- Terminal is the one place the full question, prompt, and
options are ever displayed (see ``application/owner_handoff/questions/
terminal.py``).

Optional, explicit, human-invoked-only real modes:

- ``--research real`` uses the existing A2A client against a running
  agent-skeleton (default ``http://127.0.0.1:9110``).
- ``--executor codex`` uses the real Codex CLI, sandboxed against the
  physical duplicate, and only after a matching CODEX_DATA YES.

Neither real mode is ever exercised by this repository's automated tests --
every test uses ``--executor fake`` / the default fake research client.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from application.config import load_application_environment
from application.owner_handoff.adapters.radar import SimulatorRadar
from application.owner_handoff.adapters.wearable import WearableSimulator
from application.owner_handoff.config import load_owner_handoff_config
from application.owner_handoff.domain.execution import SkillKind
from application.owner_handoff.domain.presence import (
    RadarState,
    WearableAnswer,
    WearableButton,
    WearableProximityState,
)
from application.owner_handoff.domain.work_context import WorkContext, WorkContextTracker
from application.owner_handoff.execution.coding_executor import FakeCodingExecutor
from application.owner_handoff.execution.package_installer import PackageInstaller
from application.owner_handoff.execution.permission import PermissionGate
from application.owner_handoff.execution.research_executor import ResearchAgentExecutor
from application.owner_handoff.fusion.leave_detector import LeaveDetector
from application.owner_handoff.fusion.return_detector import ReturnDetector
from application.owner_handoff.orchestrator import NoRoutedOptionError, OwnerHandoffOrchestrator
from application.owner_handoff.persistence import TaskArtifactStore
from application.owner_handoff.questions.terminal import render_question, wearable_payload
from application.owner_handoff.return_coordinator import ExecutionSession, ReturnCoordinator
from application.owner_handoff.state_machine import OwnerHandoffState
from application.owner_handoff.store import OwnerHandoffStore
from application.owner_handoff.workspace.path_policy import (
    PathPolicyError,
    validate_demo_output_paths,
)
from utils.time_utils import utc_now

DEFAULT_AGENT_URL = "http://127.0.0.1:9110"
DEFAULT_DEVICE_ID = "demo-wearable-1"


class DemoCommandError(RuntimeError):
    """Raised for a malformed or out-of-sequence demo command. Caught by
    the command loop and printed as a plain error -- never a traceback."""


class _FakeInstallRunner:
    """Default installer runner for ``--executor fake`` -- never actually
    installs anything; only used so the demo can show the full
    PACKAGE_INSTALL flow without touching a real environment."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(self, argv, *, cwd):
        from application.owner_handoff.execution.codex_cli import CommandResult

        self.calls.append({"argv": argv, "cwd": cwd})
        return CommandResult(0, "(simulated) install completed\n")


class DemoCalculatorFixExecutor:
    """Demo-only deterministic coding executor -- visibly meaningful,
    fixture-specific, NOT a general filesystem editor.

    Fixes the exact known bug in ``demo_workspace/sample_project/calculator.py``
    (``add`` subtracting instead of adding) inside the duplicate -- never
    the source. If that exact fixture/bug isn't present (e.g. a different
    ``--workspace`` was supplied), it creates one clearly-named file
    instead of guessing at arbitrary edits. Requires and consumes the same
    CODEX_DATA permit the real Codex path would, and checks
    ``request.stop_requested()`` before its one step, for the same
    deterministic step-control tests as ``MultiStepFakeCodingExecutor``.
    """

    def __init__(self) -> None:
        self.requests: list = []

    @property
    def call_count(self) -> int:
        return len(self.requests)

    def execute(self, request):
        from application.owner_handoff.domain.execution import ExecutionResult, ExecutionStatus
        from application.owner_handoff.execution.permission import (
            PermissionNotGrantedError,
            compute_manifest_hash,
        )
        from application.owner_handoff.state_machine import PermissionKind

        self.requests.append(request)

        live_digest = compute_manifest_hash(request.duplicate_path)
        try:
            request.permission_gate.consume(
                task_id=request.task_id, permission_kind=PermissionKind.CODEX_DATA,
                session_id=request.session_id, duplicate_path=request.duplicate_path,
                live_digest=live_digest, now=utc_now(), question_id=request.question_id,
            )
        except PermissionNotGrantedError as exc:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                summary=f"no valid CODEX_DATA permit; refusing to run: {exc}",
            )

        if request.stop_requested():
            return ExecutionResult(
                status=ExecutionStatus.CANCELED,
                summary="stopped before any step (owner return)",
            )

        calculator_path = request.duplicate_path / "calculator.py"
        if calculator_path.exists() and "return a - b" in calculator_path.read_text():
            calculator_path.write_text(
                calculator_path.read_text().replace("return a - b", "return a + b")
            )
            return ExecutionResult(
                status=ExecutionStatus.COMPLETED,
                summary="fixed calculator.py: add(a, b) now returns a + b",
                detail={"changed_file": "calculator.py"},
            )

        marker_path = request.duplicate_path / "AI_GENERATED_NOTE.txt"
        marker_path.write_text(
            "AI Desk demo: no matching known-bug fixture found in this "
            "workspace; nothing to fix.\n"
        )
        return ExecutionResult(
            status=ExecutionStatus.COMPLETED,
            summary="no matching fixture found; created AI_GENERATED_NOTE.txt",
            detail={"changed_file": "AI_GENERATED_NOTE.txt"},
        )


class _FakeMonotonic:
    """Deterministic, manually-advanced substitute for wall-clock time --
    the ``away`` command advances this explicitly instead of sleeping."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class DemoContext:
    orchestrator: OwnerHandoffOrchestrator
    radar: SimulatorRadar
    wearable: WearableSimulator
    monotonic: _FakeMonotonic
    device_id: str
    idle_threshold_seconds: float
    confirmation_seconds: float
    coding_executor: object
    install_runner: object
    pending_session: "ExecutionSession | None" = None


def build_demo_orchestrator(
    *,
    workspace: Path,
    allowed_boundary: Path | None = None,
    session_root: Path | None = None,
    executor_mode: str = "fake",
    research_mode: str = "fake",
    agent_url: str = DEFAULT_AGENT_URL,
    codex_binary: str = "codex",
    device_id: str = DEFAULT_DEVICE_ID,
    db_path: Path | None = None,
) -> DemoContext:
    if workspace is None:
        raise DemoCommandError("--workspace is mandatory and is never defaulted")
    workspace = Path(workspace)

    config = load_owner_handoff_config()
    allowed_boundary = Path(allowed_boundary) if allowed_boundary is not None else workspace.resolve().parent
    session_root = Path(session_root) if session_root is not None else config.workspace_session_root
    db_path = Path(db_path) if db_path is not None else config.owner_handoff_db_path

    # One pure validation pass -- no mkdir, no SQLite connection, no
    # manifest write -- BEFORE any filesystem mutation whatsoever. A
    # rejected configuration must leave `workspace` byte-for-byte,
    # tree-for-tree unchanged.
    try:
        _, allowed_boundary, session_root, db_path = validate_demo_output_paths(
            source=workspace, allowed_boundary=allowed_boundary,
            session_root=session_root, db_path=db_path,
        )
    except PathPolicyError as exc:
        raise DemoCommandError(f"unsafe configuration: {exc}") from exc

    session_root.mkdir(parents=True, exist_ok=True)
    store = OwnerHandoffStore(db_path)
    monotonic = _FakeMonotonic()
    leave_detector = LeaveDetector(
        input_idle_threshold_seconds=config.input_idle_threshold_seconds,
        owner_leave_confirmation_seconds=config.owner_leave_confirmation_seconds,
        monotonic=monotonic,
    )
    return_detector = ReturnDetector()
    radar = SimulatorRadar()
    wearable = WearableSimulator(device_id)

    context = WorkContext(
        project="AI Desk demo",
        current_task="calculator bug",
        possible_next_steps=(
            "Fix the failing test in calculator.py",
            "Research alternative unit test frameworks",
        ),
        confidence=0.4,
        updated_at=utc_now(),
    )
    work_context_tracker = WorkContextTracker(context=context)

    from application.owner_handoff.questions.generator import HandoffQuestionGenerator
    from application.owner_handoff.questions.lifecycle import QuestionLifecycle

    question_generator = HandoffQuestionGenerator(
        question_expiration_seconds=config.handoff_question_expiration_seconds,
    )
    lifecycle = QuestionLifecycle(device_allowlist=(device_id,))
    permission_gate = PermissionGate()

    if research_mode == "real":
        from application.handoff.a2a_client import A2AHandoffClient

        research_executor = ResearchAgentExecutor(A2AHandoffClient(agent_url))
    else:
        research_executor = ResearchAgentExecutor(_FakeA2AClient())

    if executor_mode == "codex":
        from application.owner_handoff.execution.codex_cli import (
            CodexCLIExecutor,
            CodexPreflight,
            SubprocessCommandRunner,
            SubprocessCodexProcessRunner,
        )

        preflight = CodexPreflight(binary=codex_binary, runner=SubprocessCommandRunner())
        coding_executor = CodexCLIExecutor(preflight=preflight, runner=SubprocessCodexProcessRunner())
    else:
        # Visibly meaningful by default: actually fixes the known
        # calculator.py bug in the duplicate (never the source) rather
        # than reporting completion without changing anything.
        coding_executor = DemoCalculatorFixExecutor()

    install_runner = _FakeInstallRunner()
    package_installer = PackageInstaller(runner=install_runner)
    return_coordinator = ReturnCoordinator(store=store)
    artifact_store = TaskArtifactStore(session_root=session_root)

    orchestrator = OwnerHandoffOrchestrator(
        store=store,
        source_workspace=workspace,
        allowed_boundary=allowed_boundary,
        session_root=session_root,
        leave_detector=leave_detector,
        return_detector=return_detector,
        radar=radar,
        wearable=wearable,
        work_context_tracker=work_context_tracker,
        question_generator=question_generator,
        lifecycle=lifecycle,
        permission_gate=permission_gate,
        research_executor=research_executor,
        coding_executor=coding_executor,
        package_installer=package_installer,
        return_coordinator=return_coordinator,
        question_expiration_seconds=config.handoff_question_expiration_seconds,
        coding_max_runtime_seconds=config.coding_agent_max_runtime_seconds,
        coding_max_safe_steps=config.coding_agent_max_safe_steps,
        artifact_store=artifact_store,
    )
    return DemoContext(
        orchestrator=orchestrator,
        radar=radar,
        wearable=wearable,
        monotonic=monotonic,
        device_id=device_id,
        idle_threshold_seconds=config.input_idle_threshold_seconds,
        confirmation_seconds=config.owner_leave_confirmation_seconds,
        coding_executor=coding_executor,
        install_runner=install_runner,
    )


class _FakeA2AClient:
    """Default research backend for ``--research fake`` -- no network."""

    def send_task(self, capsule):
        from application.handoff.models import A2AResult

        return A2AResult(
            task_id="demo-a2a-task",
            context_id="demo-ctx",
            protocol_state="completed",
            artifact={
                "status": "completed",
                "executive_summary": "(simulated) Found two relevant unit test framework options.",
                "handoff_id": capsule.handoff_id,
            },
        )


def _current_answer(ctx: DemoContext, question_id: str, button: WearableButton) -> WearableAnswer:
    return WearableAnswer(
        device_id=ctx.device_id, question_id=question_id, button=button, timestamp=utc_now(),
    )


def _print_permission_prompt(question, out: TextIO) -> None:
    out.write("=" * 60 + "\n")
    out.write("AI Desk permission question\n")
    out.write("=" * 60 + "\n")
    out.write(f"{question.prompt}\n")
    if question.package_names:
        out.write(f"Packages: {', '.join(question.package_names)}\n")
        out.write(f"Exact command: {' '.join(question.proposed_argv)}\n")
    out.write("  YES) Yes\n  NO) No\n")
    out.write(f"Question ID: {question.question_id}\n")


def handle_command(ctx: DemoContext, line: str, out: TextIO) -> bool:
    """Execute one command line. Returns False to stop the loop."""

    parts = line.strip().split()
    if not parts:
        return True
    command, args = parts[0].lower(), parts[1:]
    orchestrator = ctx.orchestrator

    if command in ("quit", "exit"):
        return False

    if command == "help":
        out.write(
            "commands: start <task_id> | away | ask | select <A|B|C|D> | "
            "grant | deny | run | wait [timeout_seconds] | "
            "install <spec...> | return | "
            "finalize [selected task text] | deliver | status | report | "
            "acknowledge (reset after CANCELED/FAILED) | quit\n"
        )
        return True

    if command == "start":
        task_id = args[0] if args else "demo-task"
        orchestrator.start_task(task_id)
        out.write(f"started task {task_id!r} (state={orchestrator.current_state().value})\n")
        return True

    if command == "status":
        if not orchestrator.active:
            out.write("no active task\n")
            return True
        state = orchestrator.current_state()
        skill = None
        try:
            skill = orchestrator.routed_skill()
        except NoRoutedOptionError:
            pass
        duplicate = orchestrator._task_state.duplicate_path
        return_requested = orchestrator._return_coordinator.is_return_requested(orchestrator.task_id)
        if ctx.pending_session is None:
            execution_state = "idle"
        elif ctx.pending_session.poll():
            execution_state = "finished (use 'wait' to collect the result)"
        else:
            execution_state = "running in background"
        out.write(
            f"task={orchestrator.task_id} state={state.value} "
            f"skill={skill.value if skill is not None else 'unrouted'} "
            f"duplicate={duplicate if duplicate is not None else 'none'} "
            f"return_requested={return_requested} execution={execution_state}\n"
        )
        return True

    if command in ("acknowledge", "reset"):
        orchestrator.acknowledge_terminal_task()
        out.write("task acknowledged; ready for a new 'start'\n")
        return True

    if command == "away":
        ctx.radar.set_state(RadarState.PERSON_ABSENT)
        ctx.wearable.set_proximity(WearableProximityState.OWNER_AWAY)
        orchestrator.evaluate_presence_for_leave()
        ctx.monotonic.advance(ctx.idle_threshold_seconds)
        orchestrator.evaluate_presence_for_leave()
        ctx.monotonic.advance(ctx.confirmation_seconds)
        orchestrator.evaluate_presence_for_leave()
        out.write(f"owner away confirmed (state={orchestrator.current_state().value})\n")
        return True

    if command == "ask":
        question = orchestrator.ask_question()
        render_question(question, out)
        question_id, letters = wearable_payload(question)
        out.write(f"[wearable receives only]: question_id={question_id} letters={letters}\n")
        return True

    if command == "select":
        if not args:
            raise DemoCommandError("usage: select <A|B|C|D>")
        button = WearableButton(args[0].upper())
        pending = orchestrator._task_state.question
        if pending is None:
            raise DemoCommandError("no handoff question is pending -- run 'ask' first")
        result = orchestrator.submit_handoff_answer(
            _current_answer(ctx, pending.question_id, button)
        )
        out.write(f"answer accepted={result.accepted} state={orchestrator.current_state().value}\n")
        if not result.accepted:
            return True
        if orchestrator.current_state() is not OwnerHandoffState.AUTHORIZED:
            # Option D (or any answer that didn't reach AUTHORIZED) --
            # nothing is routed, nothing executes. routed_skill() would
            # raise here; never call it in this branch.
            out.write("option D selected (or otherwise not authorized); nothing executed\n")
            return True
        skill = orchestrator.routed_skill()
        out.write(f"routed skill: {skill.value}\n")
        if skill is SkillKind.RESEARCH:
            exec_result = orchestrator.execute_authorized_task()
            out.write(f"research result: {exec_result.status.value} -- {exec_result.summary}\n")
        elif skill is SkillKind.CODING:
            manifest = orchestrator.begin_coding_workspace_duplication()
            out.write(f"duplicate created at: {manifest.duplicate_path}\n")
            question = orchestrator.request_codex_data_authorization(
                context_summary="AI Desk demo: calculator bug"
            )
            _print_permission_prompt(question, out)
        else:
            # UNSUPPORTED/DO_NOTHING: execute_authorized_task() itself
            # transitions AUTHORIZED -> CANCELED and never runs any
            # executor -- always call it rather than merely printing a
            # message and leaving the task stuck in AUTHORIZED.
            exec_result = orchestrator.execute_authorized_task()
            out.write(
                f"route {skill.value}; nothing executed "
                f"(state={orchestrator.current_state().value})\n"
            )
        return True

    if command in ("grant", "deny"):
        from application.owner_handoff.state_machine import PermissionKind

        button = WearableButton.YES if command == "grant" else WearableButton.NO
        task_record = orchestrator._store.get_task(orchestrator.task_id)
        if task_record.state is not OwnerHandoffState.PERMISSION_PENDING:
            raise DemoCommandError(f"no permission question is pending (state={task_record.state.value})")
        if task_record.permission_kind is PermissionKind.PACKAGE_INSTALL:
            question = orchestrator._task_state.package_permission_question
            answer = _current_answer(ctx, question.question_id, button)
            result = orchestrator.submit_package_install_answer(answer)
        else:
            question = orchestrator._task_state.codex_permission_question
            answer = _current_answer(ctx, question.question_id, button)
            result = orchestrator.submit_codex_data_answer(answer)
        out.write(f"answer accepted={result.accepted} state={orchestrator.current_state().value}\n")
        if result.accepted and button is WearableButton.YES:
            if task_record.permission_kind is PermissionKind.PACKAGE_INSTALL:
                outcome = orchestrator.install_package()
                out.write(f"install outcome: succeeded={outcome.succeeded}\n")
            else:
                out.write("CODEX_DATA granted; state=EXECUTING -- use 'run' to start coding\n")
        return True

    if command == "run":
        if orchestrator.current_state() is not OwnerHandoffState.EXECUTING:
            raise DemoCommandError(
                f"nothing to run (state={orchestrator.current_state().value})"
            )
        if ctx.pending_session is not None and not ctx.pending_session.poll():
            raise DemoCommandError("a step is already running -- use 'wait' first")
        try:
            skill = orchestrator.routed_skill()
        except NoRoutedOptionError:
            raise DemoCommandError("nothing routed to run") from None
        if skill is SkillKind.CODING:
            step_fn = orchestrator.execute_coding_task
        elif skill is SkillKind.RESEARCH:
            step_fn = orchestrator.execute_authorized_task
        else:
            raise DemoCommandError(f"nothing to run for route {skill.value}")
        session = ExecutionSession(step_fn)
        session.start()
        ctx.pending_session = session
        out.write(
            "execution started in the background -- 'status'/'return' remain "
            "available; use 'wait' to block until it finishes\n"
        )
        return True

    if command == "wait":
        if ctx.pending_session is None:
            raise DemoCommandError("nothing to wait for -- run 'run' first")
        timeout = float(args[0]) if args else 30.0
        finished = ctx.pending_session.wait(timeout)
        if not finished:
            out.write(f"still running after {timeout}s\n")
            return True
        result = ctx.pending_session.result()
        ctx.pending_session = None
        out.write(f"execution result: {result.status.value} -- {result.summary}\n")
        return True

    if command == "install":
        if not args:
            raise DemoCommandError("usage: install <package-spec> [more-specs...]")
        question = orchestrator.request_package_install_authorization(
            context_summary="AI Desk demo: install request", package_specs=list(args),
        )
        _print_permission_prompt(question, out)
        return True

    if command == "return":
        orchestrator.request_return()
        out.write(f"return requested (state={orchestrator.current_state().value})\n")
        return True

    if command == "finalize":
        selected_task = " ".join(args) if args else orchestrator.routed_goal_text()
        report = orchestrator.finalize_return(selected_task=selected_task)
        out.write(json.dumps(report.as_dict(), indent=2) + "\n")
        return True

    if command == "report":
        # Reload the exact persisted report -- useful after `attach` to a
        # task that reached READY_FOR_REVIEW in a prior process.
        persisted = orchestrator.load_persisted_final_report()
        if persisted is None:
            out.write("no persisted report available for this task\n")
        else:
            out.write(json.dumps(persisted, indent=2) + "\n")
        return True

    if command == "deliver":
        orchestrator.deliver_control()
        out.write("control returned to owner\n")
        return True

    raise DemoCommandError(f"unknown command: {command!r} (try 'help')")


def run_commands(ctx: DemoContext, commands: list[str], out: TextIO) -> bool:
    """Runs each command in order. Returns True iff every command
    succeeded (no command produced an error) -- callers (in particular
    ``main``'s scripted mode) use this to decide the process exit code.

    Any exception -- expected (``DemoCommandError``) or not (a stale/out-
    of-order orchestrator call, a transition conflict, ...) -- is caught
    here and printed as a single concise, sanitized ``error:`` line, never
    a raw traceback.
    """

    all_ok = True
    for line in commands:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.write(f"> {stripped}\n")
        try:
            if not handle_command(ctx, stripped, out):
                break
        except Exception as exc:  # noqa: BLE001 -- a CLI must never crash on a bad command
            from application.owner_handoff.domain.work_context import redact_sensitive

            out.write(f"error: {type(exc).__name__}: {redact_sensitive(str(exc))}\n")
            all_ok = False
    return all_ok


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--workspace", required=True, type=Path,
        help="Mandatory: the real source workspace to duplicate. Never defaulted to cwd/repo root/home.",
    )
    parser.add_argument("--simulate", action="store_true", help="Use simulator radar/wearable (always true today).")
    parser.add_argument("--executor", choices=("fake", "codex"), default="fake")
    parser.add_argument("--research", choices=("fake", "real"), default="fake")
    parser.add_argument("--agent-url", default=DEFAULT_AGENT_URL)
    parser.add_argument("--codex-binary", default="codex")
    parser.add_argument("--device-id", default=DEFAULT_DEVICE_ID)
    parser.add_argument("--allowed-boundary", type=Path, default=None)
    parser.add_argument("--session-root", type=Path, default=None)
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument(
        "--script", type=Path, default=None,
        help="Read commands from this file instead of stdin (one command per line).",
    )
    parser.add_argument(
        "--preflight-only", action="store_true",
        help=(
            "Only run 'codex --version' and 'codex exec --help' and report "
            "supported/missing isolation flags, then exit -- never creates a "
            "duplicate, a task database, a CODEX_DATA question, or contacts A2A."
        ),
    )
    return parser


def run_preflight_only(*, codex_binary: str, runner, out: TextIO) -> bool:
    """Runs ONLY the two inert, read-only preflight commands
    (``codex --version`` / ``codex exec --help``) and reports which
    required isolation flags the installed CLI advertises.

    Structurally never creates a duplicate, a task database, a CODEX_DATA
    question, or contacts A2A -- it does not construct an orchestrator,
    a store, or a workspace duplicator at all. Automated tests must always
    pass a fake ``runner`` here; ``main()``'s ``--preflight-only`` wiring
    is the only caller that uses a real one.
    """

    from application.owner_handoff.execution.codex_cli import (
        REQUIRED_FLAGS,
        CodexPreflight,
        CodexPreflightError,
    )

    preflight = CodexPreflight(binary=codex_binary, runner=runner)
    try:
        capabilities = preflight.run()
    except CodexPreflightError as exc:
        # CodexPreflight.run() itself already fails closed (raises) the
        # instant any required isolation flag isn't advertised -- report
        # that plainly rather than claiming success.
        out.write(f"preflight failed: {exc}\n")
        return False

    out.write(f"codex version: {capabilities.version}\n")
    out.write(f"required flags supported: {sorted(REQUIRED_FLAGS & capabilities.supported_flags)}\n")
    out.write("all required isolation flags are supported\n")
    return True


def main(argv: list[str] | None = None) -> int:
    # Match the rest of the application entry points: load the repository's
    # optional .env without overriding values the operator already exported.
    # This makes the macOS README's `cp .env.example .env` flow effective.
    load_application_environment()
    args = _build_arg_parser().parse_args(argv)

    if args.preflight_only:
        from application.owner_handoff.execution.codex_cli import SubprocessCommandRunner

        ok = run_preflight_only(
            codex_binary=args.codex_binary, runner=SubprocessCommandRunner(), out=sys.stdout,
        )
        return 0 if ok else 1

    try:
        ctx = build_demo_orchestrator(
            workspace=args.workspace,
            allowed_boundary=args.allowed_boundary,
            session_root=args.session_root,
            executor_mode=args.executor,
            research_mode=args.research,
            agent_url=args.agent_url,
            codex_binary=args.codex_binary,
            device_id=args.device_id,
            db_path=args.db_path,
        )
    except (DemoCommandError, PathPolicyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    sys.stdout.write(
        "AI Desk V2 demo ready. Type 'help' for commands, 'quit' to exit.\n"
    )
    if args.script is not None:
        commands = args.script.read_text().splitlines()
        all_ok = run_commands(ctx, commands, sys.stdout)
        return 0 if all_ok else 1

    while True:
        try:
            line = input("aidesk> ")
        except EOFError:
            break
        try:
            if not handle_command(ctx, line, sys.stdout):
                break
        except Exception as exc:  # noqa: BLE001 -- never a raw traceback in the REPL
            from application.owner_handoff.domain.work_context import redact_sensitive

            print(f"error: {type(exc).__name__}: {redact_sensitive(str(exc))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
