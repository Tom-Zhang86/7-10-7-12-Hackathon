"""Leave detection / fusion (Master Spec section 7).

A leave may be confirmed only when all four conditions hold *continuously*:
radar == PERSON_ABSENT, wearable == OWNER_AWAY, mouse idle for at least
``input_idle_threshold_seconds``, and keyboard idle for at least the same.
The 10-second confirmation window (``owner_leave_confirmation_seconds``)
starts only once all four are true — so with the defaults (5s idle + 10s
confirmation), the normal case is roughly 15 seconds after the last input,
assuming radar/wearable are already absent by then.

One signal is never enough. Any UNKNOWN or contradictory signal, or renewed
mouse/keyboard activity, resets the confirmation window entirely (this is
"sensor flapping resets the continuous timer") — there is no partial credit
carried across a reset.

Uses monotonic time internally (never wall-clock) so a system clock
adjustment cannot manufacture or erase elapsed time; the monotonic source
itself is injectable for deterministic tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from uuid import uuid4
import time

from application.owner_handoff.domain.presence import RadarState, WearableProximityState
from application.owner_handoff.state_machine import OwnerHandoffState
from application.owner_handoff.store import OwnerHandoffStore, StaleTransitionError


def _default_event_id_factory() -> str:
    return f"leave-episode-{uuid4()}"


@dataclass(frozen=True)
class LeaveEvaluation:
    """Result of one ``LeaveDetector.evaluate()`` call.

    ``episode_id`` is stable for the lifetime of one continuous
    all-conditions-true window (it is generated once when the window opens
    and reused on every subsequent tick of the same window) — this is what
    guarantees "one absence event starts at most one task" and "the same
    confirmation_event_id must not start two handoff tasks" downstream: as
    long as ``confirmation_event_id`` doesn't change, replaying it through
    ``OwnerHandoffStore.apply_transition`` is idempotent by construction.
    """

    candidate: bool
    confirmed: bool
    reverted: bool
    episode_id: str | None
    seconds_in_window: float | None

    @property
    def candidate_event_id(self) -> str | None:
        return f"leave-candidate:{self.episode_id}" if self.episode_id else None

    @property
    def confirmation_event_id(self) -> str | None:
        if self.confirmed and self.episode_id:
            return f"leave-confirmed:{self.episode_id}"
        return None


class LeaveDetector:
    """Deterministic leave-confirmation fusion over radar/wearable/input state."""

    def __init__(
        self,
        *,
        input_idle_threshold_seconds: float,
        owner_leave_confirmation_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
        event_id_factory: Callable[[], str] | None = None,
        initial_activity_at: float | None = None,
    ) -> None:
        self._idle_threshold = input_idle_threshold_seconds
        self._confirmation_seconds = owner_leave_confirmation_seconds
        self._monotonic = monotonic
        self._event_id_factory = event_id_factory or _default_event_id_factory

        # Phase 2 repair: a freshly constructed detector must NOT treat the
        # mouse/keyboard as having been idle forever -- that would let a
        # process starting while the owner happens to already be away skip
        # the input_idle_threshold_seconds wait entirely. Idle timers start
        # at construction time (or an explicit caller-supplied timestamp,
        # for a process that knows the real last-input time), never at
        # "no data ever recorded."
        start = initial_activity_at if initial_activity_at is not None else monotonic()
        self._last_mouse_activity_at: float = start
        self._last_keyboard_activity_at: float = start
        self._condition_since: float | None = None
        self._episode_active = False
        self._episode_id: str | None = None
        # One-shot guard: True once this episode has already reported
        # confirmed=True exactly once (Phase 2 repair -- previously every
        # tick past the 10s mark reported confirmed=True again).
        self._episode_confirmed = False
        self._last_seen_monotonic: float | None = None

    def record_mouse_activity(self) -> None:
        self._last_mouse_activity_at = self._monotonic()

    def record_keyboard_activity(self) -> None:
        self._last_keyboard_activity_at = self._monotonic()

    def reset(self) -> None:
        """Call after a confirmed return: the next leave gets a fresh
        episode_id (and therefore a new confirmation_event_id), per Master
        Spec section 8's "one absence event starts at most one task" and
        the Phase 2 requirement that a second leave after a confirmed
        return produce a new event ID."""

        self._condition_since = None
        self._episode_active = False
        self._episode_id = None
        self._episode_confirmed = False

    def _is_idle(self, last_activity_at: float, now: float) -> bool:
        return (now - last_activity_at) >= self._idle_threshold

    def evaluate(
        self, *, radar: RadarState, wearable: WearableProximityState
    ) -> LeaveEvaluation:
        now = self._monotonic()

        # Monotonic clock rollback: fail safe. Never let a corrupted
        # elapsed-time computation confirm an absence; treat this tick as
        # inconclusive and drop any in-progress window.
        if self._last_seen_monotonic is not None and now < self._last_seen_monotonic:
            was_active = self._episode_active
            self._condition_since = None
            self._episode_active = False
            self._episode_confirmed = False
            self._last_seen_monotonic = now
            return LeaveEvaluation(
                candidate=False,
                confirmed=False,
                reverted=was_active,
                episode_id=self._episode_id if was_active else None,
                seconds_in_window=None,
            )
        self._last_seen_monotonic = now

        mouse_idle = self._is_idle(self._last_mouse_activity_at, now)
        keyboard_idle = self._is_idle(self._last_keyboard_activity_at, now)
        all_true = (
            radar is RadarState.PERSON_ABSENT
            and wearable is WearableProximityState.OWNER_AWAY
            and mouse_idle
            and keyboard_idle
        )

        if not all_true:
            was_active = self._episode_active
            ending_episode_id = self._episode_id if was_active else None
            self._condition_since = None
            self._episode_active = False
            self._episode_confirmed = False
            return LeaveEvaluation(
                candidate=False,
                confirmed=False,
                reverted=was_active,
                episode_id=ending_episode_id,
                seconds_in_window=None,
            )

        if not self._episode_active:
            self._episode_active = True
            self._episode_id = self._event_id_factory()
            self._condition_since = now
            self._episode_confirmed = False

        elapsed = now - self._condition_since
        # >= , not >: confirm at exactly 10.0 seconds, not 9.999.
        # One-shot: only the FIRST tick that crosses the threshold reports
        # confirmed=True. Every later tick of the same episode -- even
        # though the window has objectively been open long enough -- must
        # report confirmed=False, so a caller polling in a loop never sees
        # a second "new" confirmation for the same absence episode.
        newly_confirmed = (not self._episode_confirmed) and (
            elapsed >= self._confirmation_seconds
        )
        if newly_confirmed:
            self._episode_confirmed = True
        return LeaveEvaluation(
            candidate=True,
            confirmed=newly_confirmed,
            reverted=False,
            episode_id=self._episode_id,
            seconds_in_window=elapsed,
        )


def advance_state_machine_for_leave_evaluation(
    store: OwnerHandoffStore,
    task_id: str,
    evaluation: LeaveEvaluation,
) -> None:
    """Thin state-machine integration for leave confirmation.

    Drives OBSERVING -> LEFT_CANDIDATE -> OWNER_LEFT_CONFIRMED from a
    ``LeaveEvaluation``. This is not the final orchestrator: it does not
    generate a handoff question, does not route to an executor, and simply
    no-ops (catching ``StaleTransitionError``) when the task has already
    moved past the state this evaluation is trying to reach — later ticks
    of an already-resolved episode are expected to do nothing.
    """

    if evaluation.reverted:
        try:
            store.apply_transition(
                task_id=task_id,
                event_id=f"leave-revert:{evaluation.episode_id}",
                from_state=OwnerHandoffState.LEFT_CANDIDATE,
                to_state=OwnerHandoffState.OBSERVING,
                reason="absence signals no longer unanimous",
            )
        except StaleTransitionError:
            pass
        return

    if evaluation.candidate:
        try:
            store.apply_transition(
                task_id=task_id,
                event_id=evaluation.candidate_event_id,
                from_state=OwnerHandoffState.OBSERVING,
                to_state=OwnerHandoffState.LEFT_CANDIDATE,
                reason="partial absence signal detected",
            )
        except StaleTransitionError:
            pass

    if evaluation.confirmed:
        try:
            store.apply_transition(
                task_id=task_id,
                event_id=evaluation.confirmation_event_id,
                from_state=OwnerHandoffState.LEFT_CANDIDATE,
                to_state=OwnerHandoffState.OWNER_LEFT_CONFIRMED,
                reason="10s confirmation window elapsed with unanimous absence",
            )
        except StaleTransitionError:
            pass
