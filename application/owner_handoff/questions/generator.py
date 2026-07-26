"""Deterministic, context-based handoff question generator (Master Spec
section 9).

Options come only from ``WorkContext.possible_next_steps`` and
``unfinished_work`` — never from a fixed, generic "things people usually do"
list. If fewer than two genuine, deduplicated candidates exist, or
confidence is too low to trust them, the question offers D only (a valid
outcome per the Master Spec: "if confidence is insufficient, offer
clarification or D only").
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable
from uuid import uuid4

from application.owner_handoff.domain.question import HandoffQuestion
from application.owner_handoff.domain.work_context import WorkContext
from utils.time_utils import utc_now

# Below this confidence, WorkContext's evidence is not corroborated enough
# to safely suggest specific resumption directions — Master Spec section 9:
# "if confidence is insufficient, offer clarification or D only." This
# threshold is deliberately conservative: WorkContextTracker only raises
# confidence above 0.0 once evidence is corroborated at least twice, so
# anything below this line reflects at most weak, single-source signal.
MIN_CONFIDENCE_FOR_DIRECTIONS = 0.2

MAX_DIRECTIONS = 3
MIN_DIRECTIONS = 2

_PROMPT = "The owner appears to have stepped away. What should AI Desk do?"


def _default_question_id_factory() -> str:
    return f"question-{uuid4()}"


def _normalize_for_dedup(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _deduplicate(candidates: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for candidate in candidates:
        key = _normalize_for_dedup(candidate)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(candidate.strip())
    return result


class HandoffQuestionGenerator:
    """Builds one ``HandoffQuestion`` from a frozen ``WorkContext`` snapshot."""

    def __init__(
        self,
        *,
        question_expiration_seconds: float,
        clock: Callable[[], datetime] = utc_now,
        question_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._expiration_seconds = question_expiration_seconds
        self._clock = clock
        self._question_id_factory = question_id_factory or _default_question_id_factory

    def generate(self, context: WorkContext) -> HandoffQuestion:
        """Generate one question. ``context`` is used as a frozen snapshot —
        this function never mutates it and never claims any of its
        unfinished/next-step entries represents completed work."""

        now = self._clock()
        directions = self._derive_directions(context)

        options: dict[str, str] = {}
        for letter, text in zip(("A", "B", "C"), directions):
            options[letter] = text
        options["D"] = "Do nothing"

        return HandoffQuestion(
            question_id=self._question_id_factory(),
            context_summary=self._summarize(context),
            question=_PROMPT,
            options=options,
            created_at=now,
            expires_at=now + timedelta(seconds=self._expiration_seconds),
        )

    def _derive_directions(self, context: WorkContext) -> list[str]:
        if context.confidence < MIN_CONFIDENCE_FOR_DIRECTIONS:
            return []

        candidates: list[str] = list(context.possible_next_steps)
        candidates.extend(f"Continue: {item}" for item in context.unfinished_work)

        deduped = _deduplicate(candidates)
        if len(deduped) < MIN_DIRECTIONS:
            # Not enough genuine, distinct context-derived directions to
            # offer a real choice — D-only is the safe, honest fallback
            # rather than padding with a generic suggestion.
            return []
        return deduped[:MAX_DIRECTIONS]

    def _summarize(self, context: WorkContext) -> str:
        # Built purely from already-sanitized WorkContext fields (every
        # field is validated at construction — see domain/work_context.py)
        # — there is nothing further to redact here.
        parts = [part for part in (context.project, context.current_task) if part]
        summary = " — ".join(parts)
        if context.stage:
            summary = f"{summary} ({context.stage})" if summary else context.stage
        return summary or "No corroborated project or task context yet."
