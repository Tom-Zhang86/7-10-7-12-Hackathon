"""Skill routing and execution result domain models (Master Spec section 9,
12).

These are pure, immutable contracts. Routing decisions never change the
external ``HandoffQuestion`` JSON contract (Master Spec section 9) — a
``HandoffPlan`` is a companion object describing internal route metadata
only, produced from a question after the fact.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class SkillKind(str, Enum):
    """Conservative routing outcome for one handoff option."""

    RESEARCH = "research"
    CODING = "coding"
    DO_NOTHING = "do_nothing"
    # Ambiguous, conflicting, or unrecognized text. This is a fail-closed
    # outcome, not an error — no executor may ever run for this skill.
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class RoutedOption:
    """One option letter's routing decision, bound to the exact text it was
    derived from (for audit — never re-derived from a stale copy)."""

    letter: str
    skill: SkillKind
    goal_text: str

    def __post_init__(self) -> None:
        if self.letter not in {"A", "B", "C", "D"}:
            raise ValueError("letter must be one of A, B, C, D")
        if not isinstance(self.skill, SkillKind):
            raise TypeError("skill must be a SkillKind")
        if not isinstance(self.goal_text, str) or not self.goal_text.strip():
            raise ValueError("goal_text must be a non-empty string")


@dataclass(frozen=True)
class HandoffPlan:
    """Companion routing plan for one ``HandoffQuestion``. Never part of the
    question's own JSON contract."""

    question_id: str
    routes: Mapping[str, RoutedOption]

    def __post_init__(self) -> None:
        if not self.question_id or not self.question_id.strip():
            raise ValueError("question_id must be a non-empty string")
        if not isinstance(self.routes, Mapping) or not self.routes:
            raise ValueError("routes must be a non-empty mapping")
        for letter, routed in self.routes.items():
            if not isinstance(routed, RoutedOption) or routed.letter != letter:
                raise ValueError(f"routes[{letter!r}] must be a matching RoutedOption")
        object.__setattr__(self, "routes", MappingProxyType(dict(self.routes)))

    def route_for(self, letter: str) -> RoutedOption:
        return self.routes[letter]


class ExecutionStatus(str, Enum):
    """Terminal outcome of one executor invocation."""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass(frozen=True)
class ExecutionResult:
    """Uniform result contract both ResearchAgentExecutor and
    CodingAgentExecutor (fake or real Codex) return, so a future
    orchestrator can treat them identically."""

    status: ExecutionStatus
    summary: str
    detail: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not isinstance(self.status, ExecutionStatus):
            raise TypeError("status must be an ExecutionStatus")
        if not isinstance(self.summary, str):
            raise TypeError("summary must be a string")
        if not isinstance(self.detail, Mapping):
            raise TypeError("detail must be a mapping")
        object.__setattr__(self, "detail", MappingProxyType(dict(self.detail)))
