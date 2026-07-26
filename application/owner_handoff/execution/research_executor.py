"""ResearchAgentExecutor: an owner_handoff-side adapter around the existing
A2A Research Handoff Agent (Master Spec section 12).

This does not rewrite ``application/handoff/``'s A2A client, models, or
protocol — it wraps/injects ``application.handoff.a2a_client.A2AHandoffClient``
and reuses the existing ``TaskCapsule`` schema unchanged. Only the explicitly
selected research goal and required capsule metadata are ever sent; no
workspace file, duplicate path, or manifest is ever part of the payload —
research delegation never touches the filesystem duplication machinery at
all.

Cancellation semantics (Phase 4 repair): a dispatched A2A request is treated
as one atomic remote step -- ``execute()`` has no way to cancel a request
already sent to the remote agent (the A2A protocol/client are unchanged, and
no cancellation endpoint is introduced here). ``ReturnCoordinator.run_current_step``
therefore never receives a ``terminate_fn`` for a research step: if owner
return is confirmed while a request is in flight and the grace period
elapses before the remote agent responds, the coordinator raises
``StepDidNotFinishInGracePeriodError`` -- reported honestly as "did not
finish," never claimed as "terminated" -- and the underlying call is simply
abandoned on its own daemon thread (never blocking process exit) rather
than pretended to have stopped. The orchestrator never dispatches a second
research request once return has been requested for a task.
"""
from __future__ import annotations

from typing import Protocol

from application.handoff.models import A2AResult, TaskCapsule
from application.owner_handoff.domain.execution import ExecutionResult, ExecutionStatus
from application.owner_handoff.domain.work_context import redact_sensitive


class A2AClientProtocol(Protocol):
    """Structural match for ``application.handoff.a2a_client.A2AHandoffClient``
    — tests inject a fake implementing just this."""

    def send_task(self, capsule: TaskCapsule) -> A2AResult:
        ...


class ResearchAgentExecutor:
    """Delegates one routed research option to the existing Research Agent."""

    def __init__(self, client: A2AClientProtocol) -> None:
        self._client = client

    def execute(self, *, goal_text: str, expected_output: str | None = None) -> ExecutionResult:
        """Send only the goal text (and optional expected-output hint) as a
        new ``TaskCapsule`` — never the workspace, the duplicate, or any
        file contents."""

        capsule = TaskCapsule.create(
            goal_text,
            expected_output=expected_output,
        )
        try:
            result = self._client.send_task(capsule)
        except Exception as exc:  # noqa: BLE001 - map any client failure uniformly
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                summary=(
                    f"Research Agent delegation failed: {type(exc).__name__}: "
                    f"{redact_sensitive(str(exc))}"
                ),
                detail={"handoff_id": capsule.handoff_id},
            )

        artifact_status = str(result.artifact.get("status") or "")
        if artifact_status == "completed":
            status = ExecutionStatus.COMPLETED
            summary = redact_sensitive(
                str(result.artifact.get("executive_summary") or "Research task completed.")
            )
        elif artifact_status == "input_required":
            status = ExecutionStatus.FAILED
            summary = "Research Agent requires additional input."
        else:
            status = ExecutionStatus.FAILED
            summary = redact_sensitive(
                str(
                    result.artifact.get("executive_summary")
                    or "Research Agent returned a non-completed status."
                )
            )

        return ExecutionResult(
            status=status,
            summary=summary,
            detail={
                "handoff_id": capsule.handoff_id,
                "a2a_task_id": result.task_id,
                "context_id": result.context_id,
                "protocol_state": result.protocol_state,
            },
        )
