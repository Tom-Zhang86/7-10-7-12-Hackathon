"""Return detection (Master Spec section 17).

Confirm owner return only when radar == PERSON_PRESENT and wearable ==
OWNER_NEAR. Mouse/keyboard activity may be recorded as supporting evidence,
but it can never override an UNKNOWN or contradictory hardware state — it
is purely informational metadata on the result, never part of the
confirmation gate itself.

Phase 2 scope note: unlike leave detection, this module is not wired into
the persisted state machine — the Phase 2 task explicitly scopes "thin
state-machine integration" to leave confirmation and question
authorization/cancellation only. ``ReturnDetector`` is a standalone,
independently testable fusion component; wiring a confirmed return into
EXECUTING -> RETURN_REQUESTED (and the rest of the stop/resume behavior) is
left to the phase that builds the return coordinator and top-level
orchestrator.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from uuid import uuid4

from application.owner_handoff.domain.presence import RadarState, WearableProximityState


def _default_event_id_factory() -> str:
    return f"return-episode-{uuid4()}"


@dataclass(frozen=True)
class ReturnEvaluation:
    confirmed: bool
    event_id: str | None
    supporting_evidence: bool


class ReturnDetector:
    """Deterministic return-confirmation fusion over radar/wearable state."""

    def __init__(self, *, event_id_factory: Callable[[], str] | None = None) -> None:
        self._event_id_factory = event_id_factory or _default_event_id_factory
        self._already_confirmed = False

    def evaluate(
        self,
        *,
        radar: RadarState,
        wearable: WearableProximityState,
        mouse_active: bool = False,
        keyboard_active: bool = False,
    ) -> ReturnEvaluation:
        confirmed_now = (
            radar is RadarState.PERSON_PRESENT
            and wearable is WearableProximityState.OWNER_NEAR
        )
        supporting_evidence = bool(mouse_active or keyboard_active)

        if not confirmed_now or self._already_confirmed:
            # Repeated PRESENT+NEAR samples never re-emit while the owner
            # remains present; an explicit reset() is required to re-arm.
            return ReturnEvaluation(
                confirmed=False, event_id=None, supporting_evidence=supporting_evidence
            )

        self._already_confirmed = True
        return ReturnEvaluation(
            confirmed=True,
            event_id=self._event_id_factory(),
            supporting_evidence=supporting_evidence,
        )

    def reset(self) -> None:
        """Call once a new leave episode begins, allowing a later return to
        be confirmed again and emit a fresh event id."""

        self._already_confirmed = False
