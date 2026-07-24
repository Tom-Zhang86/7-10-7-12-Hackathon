from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from utils.time_utils import utc_now


REQUEST_SCHEMA = "aidesk.research-handoff.request.v1"
RESULT_SCHEMA = "aidesk.research-handoff.result.v1"


class HandoffStatus(str, Enum):
    ARMED = "armed"
    DELEGATING = "delegating"
    RUNNING = "running"
    READY = "ready"
    INPUT_REQUIRED = "input_required"
    FAILED = "failed"
    RETURNED = "returned"
    CANCELED = "canceled"


@dataclass(frozen=True)
class HandoffInput:
    kind: str
    value: str

    def __post_init__(self) -> None:
        if self.kind not in {"text", "url"}:
            raise ValueError("handoff input kind must be text or url")
        if not self.value.strip():
            raise ValueError("handoff input value cannot be empty")

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "value": self.value}


@dataclass(frozen=True)
class TaskCapsule:
    handoff_id: str
    goal: str
    inputs: tuple[HandoffInput, ...] = ()
    expected_output: str = "A cited research brief with concrete next steps."
    agent_skill: str = "research/handoff"
    constraints: dict[str, Any] = field(
        default_factory=lambda: {
            "max_sources": 8,
            "time_budget_seconds": 120,
        }
    )
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.handoff_id.strip():
            raise ValueError("handoff_id cannot be empty")
        if not self.goal.strip():
            raise ValueError("goal cannot be empty")
        if not self.agent_skill.strip():
            raise ValueError("agent_skill cannot be empty")

    @classmethod
    def create(
        cls,
        goal: str,
        *,
        inputs: list[HandoffInput] | tuple[HandoffInput, ...] = (),
        expected_output: str | None = None,
        agent_skill: str = "research/handoff",
        constraints: dict[str, Any] | None = None,
    ) -> "TaskCapsule":
        values: dict[str, Any] = {
            "handoff_id": str(uuid4()),
            "goal": goal.strip(),
            "inputs": tuple(inputs),
            "agent_skill": agent_skill,
        }
        if expected_output is not None:
            values["expected_output"] = expected_output.strip()
        if constraints is not None:
            values["constraints"] = dict(constraints)
        return cls(**values)

    def as_payload(self) -> dict[str, Any]:
        return {
            "schema_version": REQUEST_SCHEMA,
            "handoff_id": self.handoff_id,
            "goal": self.goal,
            "inputs": [item.as_dict() for item in self.inputs],
            "expected_output": self.expected_output,
            "agent_skill": self.agent_skill,
            "constraints": dict(self.constraints),
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class HandoffRecord:
    capsule: TaskCapsule
    status: HandoffStatus
    a2a_task_id: str = ""
    context_id: str = ""
    artifact: dict[str, Any] | None = None
    error: str = ""
    updated_at: datetime = field(default_factory=utc_now)
    delegated_at: datetime | None = None
    completed_at: datetime | None = None
    returned_at: datetime | None = None


@dataclass(frozen=True)
class A2AResult:
    task_id: str
    context_id: str
    protocol_state: str
    artifact: dict[str, Any]
