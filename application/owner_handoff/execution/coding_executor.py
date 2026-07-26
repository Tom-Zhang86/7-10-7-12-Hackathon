"""CodingAgentExecutor interface and deterministic fake (Master Spec
section 12).

The Research Agent is never converted into a general coding agent — coding
selections always go through this separate interface. Normal tests use only
``FakeCodingExecutor``; the real implementation is ``CodexCLIExecutor``
(``execution/codex_cli.py``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol

from application.owner_handoff.domain.execution import ExecutionResult, ExecutionStatus

if TYPE_CHECKING:
    from application.owner_handoff.execution.permission import PermissionGate


def _never_stop() -> bool:
    return False


@dataclass(frozen=True)
class CodingTaskRequest:
    """A coding task request.

    Repair (Phase 3 gate, permission enforcement): a coding task is always
    bound to the exact CODEX_DATA permit that authorized it -- ``task_id``,
    ``session_id``, and ``question_id`` identify which permit
    ``permission_gate`` must ``consume`` immediately before the real
    executor (``CodexCLIExecutor``) invokes any subprocess. ``FakeCodingExecutor``
    ignores these fields; only the real executor enforces them.

    Repair (Phase 4 gate, cancellable execution): ``stop_requested`` is a
    live, zero-argument callable an executor may poll cooperatively between
    its own internal steps (e.g. once per Codex JSONL item, or once per
    fake sub-step) to learn "owner return has been confirmed -- finish the
    current step, but do not start another." It defaults to a callable that
    always returns ``False`` (never stop) so existing callers/tests are
    unaffected unless they explicitly wire it to
    ``ReturnCoordinator.is_return_requested``.
    """

    goal_text: str
    duplicate_path: Path
    max_runtime_seconds: float
    max_safe_steps: int
    task_id: str
    session_id: str
    question_id: str
    permission_gate: "PermissionGate"
    stop_requested: Callable[[], bool] = _never_stop

    def __post_init__(self) -> None:
        if not isinstance(self.goal_text, str) or not self.goal_text.strip():
            raise ValueError("goal_text must be a non-empty string")
        if self.max_runtime_seconds <= 0:
            raise ValueError("max_runtime_seconds must be positive")
        if self.max_safe_steps <= 0:
            raise ValueError("max_safe_steps must be positive")
        for name in ("task_id", "session_id", "question_id"):
            if not getattr(self, name):
                raise ValueError(f"{name} must be a non-empty string")


class CodingAgentExecutor(Protocol):
    def execute(self, request: CodingTaskRequest) -> ExecutionResult:
        ...


class FakeCodingExecutor:
    """Deterministic fake. Never invokes any subprocess, thread, or real
    Codex CLI — every call is a plain in-memory lookup."""

    def __init__(
        self,
        *,
        result: ExecutionResult | None = None,
        script: list[ExecutionResult] | None = None,
    ) -> None:
        self._result = result or ExecutionResult(
            status=ExecutionStatus.COMPLETED, summary="fake coding task completed"
        )
        self._script = list(script) if script is not None else None
        self.requests: list[CodingTaskRequest] = []

    @property
    def call_count(self) -> int:
        return len(self.requests)

    def execute(self, request: CodingTaskRequest) -> ExecutionResult:
        self.requests.append(request)
        if self._script is not None:
            index = min(len(self.requests) - 1, len(self._script) - 1)
            return self._script[index]
        return self._result


class MultiStepFakeCodingExecutor:
    """Deterministic, multi-step controllable fake for return-during-
    execution tests. Runs steps ``1..step_count`` in order, checking
    ``request.stop_requested()`` BEFORE starting each step (never
    mid-step) -- this is what a test uses to prove "return during step N
    finishes N but never starts N+1." Never touches any filesystem,
    subprocess, or thread."""

    def __init__(
        self, *, step_count: int, on_step: "Callable[[int], None] | None" = None
    ) -> None:
        if step_count <= 0:
            raise ValueError("step_count must be positive")
        self.step_count = step_count
        self.started_steps: list[int] = []
        self.completed_steps: list[int] = []
        self._on_step = on_step

    def execute(self, request: CodingTaskRequest) -> ExecutionResult:
        for step in range(1, self.step_count + 1):
            if request.stop_requested():
                return ExecutionResult(
                    status=ExecutionStatus.CANCELED,
                    summary=(
                        f"stopped before step {step} (owner return); "
                        f"completed steps: {self.completed_steps}"
                    ),
                    detail={"completed_steps": tuple(self.completed_steps)},
                )
            self.started_steps.append(step)
            if self._on_step is not None:
                self._on_step(step)
            self.completed_steps.append(step)
        return ExecutionResult(
            status=ExecutionStatus.COMPLETED,
            summary=f"completed all {self.step_count} steps",
            detail={"completed_steps": tuple(self.completed_steps)},
        )
