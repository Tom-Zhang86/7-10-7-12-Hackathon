"""Wearable answer validation/lifecycle and its thin state-machine
integration (Master Spec section 6, 9, 14, 15).

For Phase 2, the generic question/button validation contract is
implemented, but only the handoff-selection lifecycle (QUESTION_PENDING ->
AUTHORIZED/CANCELED) is exercised end-to-end. The CODEX_DATA and
PACKAGE_INSTALL permission lifecycles (YES/NO questions gating
PERMISSION_PENDING) reuse the same validation contract; Phase 3 wires them
to real executors via ``execution/permission.py``.

Restart-safety (Master Spec section 6/9): ``QuestionLifecycle`` holds its
one pending question in memory only — deliberately taking the fail-closed
MVP option rather than persisting a pending-question record. A restarted
process constructs a fresh ``QuestionLifecycle`` with no pending question,
so any incoming answer is rejected as ``WRONG_STATE`` — it can never
silently accept an answer for a question it cannot reconstruct.
``recover_pending_questions_after_restart`` complements this by explicitly
cancelling any task the *persisted* state machine still shows as
QUESTION_PENDING, so the task record itself doesn't sit forever waiting for
an in-memory object that no longer exists.

Phase 2 repair (authorization forgeability): the previous design exposed a
public ``advance_state_machine_for_answer(store, task_id, question, answer,
outcome)`` function that blindly trusted a caller-supplied ``outcome``
object — anyone could construct ``AnswerOutcome(accepted=True)`` directly
and hand it in, with no real validation ever having occurred. That function
has been removed. The only way to move a task out of QUESTION_PENDING from
a wearable answer is now ``QuestionLifecycle.submit_answer``, which
performs its own internal validation against the raw ``WearableAnswer`` and
never accepts an externally-supplied "this was already validated" flag.
``evaluate_answer`` still exists as a non-authoritative, side-effect-free
dry-run check (e.g. for a UI to preview whether an answer would be
accepted) — its result carries no authority and cannot be fed back in to
authorize anything.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Union

from application.owner_handoff.domain.presence import WearableAnswer, WearableButton
from application.owner_handoff.domain.question import HandoffQuestion, PermissionQuestion
from application.owner_handoff.state_machine import OwnerHandoffState, PermissionKind
from application.owner_handoff.store import OwnerHandoffStore, StaleTransitionError, TaskRecord
from utils.time_utils import utc_now

PendingQuestion = Union[HandoffQuestion, PermissionQuestion]


class QuestionKind(str, Enum):
    """Which button family a pending question expects."""

    HANDOFF_SELECTION = "handoff_selection"  # expects A/B/C/D
    PERMISSION = "permission"  # expects YES/NO (CODEX_DATA / PACKAGE_INSTALL)


_HANDOFF_BUTTONS = frozenset(
    {WearableButton.A, WearableButton.B, WearableButton.C, WearableButton.D}
)
_PERMISSION_BUTTONS = frozenset({WearableButton.YES, WearableButton.NO})

# Small, documented tolerance for clock differences between the wearable
# and this process — an answer timestamped up to this far in the future
# relative to our own clock is still accepted; anything further is
# rejected as materially-in-the-future (a symptom of a misbehaving or
# spoofed sender, not ordinary clock drift).
CLOCK_SKEW_TOLERANCE_SECONDS = 5.0


class AnswerRejectionReason(str, Enum):
    UNKNOWN_DEVICE = "unknown_device"
    WRONG_STATE = "wrong_state"
    WRONG_QUESTION_ID = "wrong_question_id"
    DUPLICATE = "duplicate"
    BEFORE_CREATION = "before_creation"
    EXPIRED = "expired"
    FUTURE_TIMESTAMP = "future_timestamp"
    INVALID_BUTTON_FAMILY = "invalid_button_family"
    OPTION_NOT_OFFERED = "option_not_offered"
    DISCONNECTED = "disconnected"


class QuestionAlreadyPendingError(RuntimeError):
    """Raised by ``start_question`` when a still-pending, unexpired
    question would be silently overwritten."""


@dataclass(frozen=True)
class AnswerOutcome:
    """A non-authoritative, side-effect-free validation result.

    This is what ``evaluate_answer`` (a dry-run check) returns. It must
    never be treated as proof that a transition occurred — only
    ``QuestionLifecycle.submit_answer``'s own internal validation can
    authorize a state-machine transition, and it does not accept this (or
    any) externally-constructed object as its input.
    """

    accepted: bool
    rejection_reason: AnswerRejectionReason | None = None


@dataclass(frozen=True)
class AnswerSubmissionResult:
    """Result of the one authoritative ``submit_answer`` call."""

    accepted: bool
    rejection_reason: AnswerRejectionReason | None
    task_record: TaskRecord | None


class QuestionLifecycle:
    """Validates wearable answers against at most one pending question, and
    is the sole authority for turning an accepted answer into a state
    transition."""

    def __init__(
        self,
        *,
        device_allowlist: tuple[str, ...],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._device_allowlist = frozenset(device_allowlist)
        self._clock = clock
        self._pending_question: PendingQuestion | None = None
        self._kind: QuestionKind | None = None
        # question_id -> (the exact WearableAnswer that was accepted, the
        # AnswerSubmissionResult produced for it). Populated only AFTER a
        # transition has actually succeeded -- never on rejection, and
        # never speculatively before the store confirms the change.
        self._accepted_answers: dict[str, tuple[WearableAnswer, AnswerSubmissionResult]] = {}
        self._connected = True

    @property
    def pending_question(self) -> PendingQuestion | None:
        return self._pending_question

    def start_question(
        self,
        question: PendingQuestion,
        *,
        kind: QuestionKind = QuestionKind.HANDOFF_SELECTION,
    ) -> None:
        """Begin tracking a new pending question.

        Repair: refuses to silently overwrite a question that is still
        pending and not yet expired — a caller must explicitly
        ``cancel_pending()`` first (or wait for expiry), so a bug elsewhere
        can never quietly discard an unanswered, still-valid question by
        starting a new one over it.
        """

        if self._pending_question is not None and self._clock() < self._pending_question.expires_at:
            raise QuestionAlreadyPendingError(
                f"question {self._pending_question.question_id} is still "
                "pending; cancel it explicitly before starting another"
            )
        self._pending_question = question
        self._kind = kind

    def cancel_pending(self) -> None:
        self._pending_question = None
        self._kind = None

    def disconnect(self) -> None:
        self._connected = False

    def reconnect(self) -> None:
        self._connected = True

    def evaluate_answer(self, answer: WearableAnswer) -> AnswerOutcome:
        """Non-authoritative dry-run validation only — see class/module
        docstring. Never mutates lifecycle state, regardless of outcome."""

        outcome, _question = self._validate(answer)
        return outcome

    def _validate(
        self, answer: WearableAnswer
    ) -> tuple[AnswerOutcome, PendingQuestion | None]:
        """Pure validation, no side effects. Returns the outcome and (if
        accepted) the pending question it was validated against."""

        if not self._connected:
            return AnswerOutcome(False, AnswerRejectionReason.DISCONNECTED), None
        if answer.device_id not in self._device_allowlist:
            return AnswerOutcome(False, AnswerRejectionReason.UNKNOWN_DEVICE), None
        if self._pending_question is None:
            return AnswerOutcome(False, AnswerRejectionReason.WRONG_STATE), None

        question = self._pending_question
        if answer.question_id != question.question_id:
            return AnswerOutcome(False, AnswerRejectionReason.WRONG_QUESTION_ID), None

        cached = self._accepted_answers.get(answer.question_id)
        if cached is not None:
            # A question that already has a confirmed transition can only
            # ever be validated again as an exact replay (handled by the
            # caller, submit_answer) -- any other answer for it is a
            # duplicate, full stop.
            cached_answer, _ = cached
            if answer != cached_answer:
                return AnswerOutcome(False, AnswerRejectionReason.DUPLICATE), None

        if answer.timestamp < question.created_at:
            return AnswerOutcome(False, AnswerRejectionReason.BEFORE_CREATION), None
        now = self._clock()
        if now >= question.expires_at or answer.timestamp >= question.expires_at:
            return AnswerOutcome(False, AnswerRejectionReason.EXPIRED), None
        if (answer.timestamp - now).total_seconds() > CLOCK_SKEW_TOLERANCE_SECONDS:
            return AnswerOutcome(False, AnswerRejectionReason.FUTURE_TIMESTAMP), None

        expected_buttons = (
            _HANDOFF_BUTTONS if self._kind is QuestionKind.HANDOFF_SELECTION else _PERMISSION_BUTTONS
        )
        if answer.button not in expected_buttons:
            return AnswerOutcome(False, AnswerRejectionReason.INVALID_BUTTON_FAMILY), None
        if answer.button.value not in question.options:
            return AnswerOutcome(False, AnswerRejectionReason.OPTION_NOT_OFFERED), None

        return AnswerOutcome(True, None), question

    def submit_answer(
        self,
        answer: WearableAnswer,
        *,
        store: OwnerHandoffStore,
        task_id: str,
        on_permission_granted: Callable[[PermissionQuestion], None] | None = None,
    ) -> AnswerSubmissionResult:
        """The one authoritative path from a raw ``WearableAnswer`` to a
        state transition.

        - Rejected answers never mutate lifecycle or task state at all.
        - An answer is marked consumed only after the store transition has
          actually succeeded -- never before, and never speculatively.
        - Replaying the *exact same* already-accepted answer (same device,
          question, button, and timestamp) is idempotent: it returns the
          original result again without attempting a second transition.
        - A materially *different* answer for a question that already has
          an accepted answer is rejected as DUPLICATE.
        - If the store transition itself raises (e.g. ``StaleTransitionError``
          because the task moved on for an unrelated reason), the answer is
          NOT marked consumed, so a later, legitimate retry remains
          possible; the exception propagates to the caller.

        For ``QuestionKind.PERMISSION`` questions, YES drives
        PERMISSION_PENDING -> EXECUTING and NO drives
        PERMISSION_PENDING -> CANCELED; ``on_permission_granted`` (if
        supplied) is invoked with the granted ``PermissionQuestion`` only
        after that transition has actually succeeded — the natural place
        for a caller (``execution/permission.py``) to record a
        task/session/manifest-bound authorization.
        """

        outcome, question = self._validate(answer)
        if not outcome.accepted:
            return AnswerSubmissionResult(False, outcome.rejection_reason, None)

        cached = self._accepted_answers.get(answer.question_id)
        if cached is not None:
            cached_answer, cached_result = cached
            if answer == cached_answer:
                return cached_result
            return AnswerSubmissionResult(False, AnswerRejectionReason.DUPLICATE, None)

        if self._kind is QuestionKind.PERMISSION:
            permission_kind = question.permission_kind
            if answer.button is WearableButton.YES:
                record = store.apply_transition(
                    task_id=task_id,
                    event_id=f"permission-granted:{question.question_id}",
                    from_state=OwnerHandoffState.PERMISSION_PENDING,
                    to_state=OwnerHandoffState.EXECUTING,
                    reason=f"{permission_kind.value} authorization granted",
                    permission_kind=permission_kind,
                )
                if on_permission_granted is not None:
                    on_permission_granted(question)
            else:
                record = store.apply_transition(
                    task_id=task_id,
                    event_id=f"permission-denied:{question.question_id}",
                    from_state=OwnerHandoffState.PERMISSION_PENDING,
                    to_state=OwnerHandoffState.CANCELED,
                    reason=f"{permission_kind.value} authorization denied",
                    permission_kind=permission_kind,
                )
        elif answer.button is WearableButton.D:
            record = store.apply_transition(
                task_id=task_id,
                event_id=f"question-cancel:{question.question_id}",
                from_state=OwnerHandoffState.QUESTION_PENDING,
                to_state=OwnerHandoffState.CANCELED,
                reason="option D selected",
            )
        else:
            record = store.apply_transition(
                task_id=task_id,
                event_id=f"question-authorized:{question.question_id}",
                from_state=OwnerHandoffState.QUESTION_PENDING,
                to_state=OwnerHandoffState.AUTHORIZED,
                reason=f"owner selected option {answer.button.value}",
            )

        # Only reached once the transition above has actually succeeded.
        result = AnswerSubmissionResult(True, None, record)
        self._accepted_answers[answer.question_id] = (answer, result)
        return result


# ---------------------------------------------------------------------------
# System-initiated cancellations (not wearable-answer-driven, so not part of
# the forgeability concern above -- these are called directly by whatever
# detects the timeout/return/disconnect condition, never by untrusted input).
# ---------------------------------------------------------------------------


def _pending_source_state(question: PendingQuestion) -> OwnerHandoffState:
    return (
        OwnerHandoffState.PERMISSION_PENDING
        if isinstance(question, PermissionQuestion)
        else OwnerHandoffState.QUESTION_PENDING
    )


def _pending_permission_kind(question: PendingQuestion) -> PermissionKind | None:
    return question.permission_kind if isinstance(question, PermissionQuestion) else None


def cancel_for_timeout(
    store: OwnerHandoffStore, task_id: str, question: PendingQuestion, *, now: datetime
) -> TaskRecord | None:
    """Timeout always resolves to "no" / CANCELED, never to authorization.
    Returns None (no-op) if the question has not actually expired yet."""

    if now < question.expires_at:
        return None
    return store.apply_transition(
        task_id=task_id,
        event_id=f"question-timeout:{question.question_id}",
        from_state=_pending_source_state(question),
        to_state=OwnerHandoffState.CANCELED,
        reason="question expired",
        permission_kind=_pending_permission_kind(question),
    )


def cancel_for_owner_return(
    store: OwnerHandoffStore, task_id: str, question: PendingQuestion
) -> TaskRecord:
    return store.apply_transition(
        task_id=task_id,
        event_id=f"question-owner-return:{question.question_id}",
        from_state=_pending_source_state(question),
        to_state=OwnerHandoffState.CANCELED,
        reason="owner returned before authorization",
        permission_kind=_pending_permission_kind(question),
    )


def cancel_for_disconnect(
    store: OwnerHandoffStore, task_id: str, question: PendingQuestion
) -> TaskRecord:
    return store.apply_transition(
        task_id=task_id,
        event_id=f"question-disconnect:{question.question_id}",
        from_state=_pending_source_state(question),
        to_state=OwnerHandoffState.CANCELED,
        reason="wearable disconnected",
        permission_kind=_pending_permission_kind(question),
    )


def recover_pending_questions_after_restart(
    store: OwnerHandoffStore,
    *,
    reason: str = "process restarted; pending question cannot be reconstructed",
    event_id_factory: Callable[[TaskRecord], str] | None = None,
) -> list[TaskRecord]:
    """Fail-closed restart recovery for QUESTION_PENDING.

    ``QuestionLifecycle`` is in-memory only (see module docstring), so a
    restarted process has no way to validate a subsequent answer against
    whatever question was actually pending before the restart. Rather than
    leave the task stuck in QUESTION_PENDING indefinitely, this cancels it
    outright — the same "never silently accept" principle
    ``OwnerHandoffStore.recover_after_restart`` applies to
    WORKSPACE_DUPLICATING/EXECUTING/PERMISSION_PENDING.
    """

    recovered: list[TaskRecord] = []
    for record in store.list_tasks_in_state(OwnerHandoffState.QUESTION_PENDING):
        event_id = (
            event_id_factory(record)
            if event_id_factory is not None
            else f"question-restart-cancel:{record.task_id}:{record.updated_at.isoformat()}"
        )
        try:
            updated = store.apply_transition(
                task_id=record.task_id,
                event_id=event_id,
                from_state=OwnerHandoffState.QUESTION_PENDING,
                to_state=OwnerHandoffState.CANCELED,
                reason=reason,
            )
            recovered.append(updated)
        except StaleTransitionError:
            continue
    return recovered
