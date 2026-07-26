import io
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from application.owner_handoff.domain.presence import WearableAnswer, WearableButton
from application.owner_handoff.domain.question import HandoffQuestion
from application.owner_handoff.domain.work_context import (
    Evidence,
    EvidenceKind,
    WorkContext,
)
from application.owner_handoff.questions.generator import HandoffQuestionGenerator
from application.owner_handoff.questions.lifecycle import (
    AnswerRejectionReason,
    QuestionLifecycle,
    cancel_for_disconnect,
    cancel_for_owner_return,
    cancel_for_timeout,
)
from application.owner_handoff.questions.terminal import render_question, wearable_payload
from application.owner_handoff.state_machine import OwnerHandoffState
from application.owner_handoff.store import OwnerHandoffStore

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
DEVICE_ID = "wearable-001"
S = OwnerHandoffState


def _evidence(source: str, detail: str, count: int = 2) -> Evidence:
    return Evidence(
        kind=EvidenceKind.OBSERVATION,
        source=source,
        detail=detail,
        observed_at=NOW,
        corroboration_count=count,
    )


def _corroborated_context(**overrides) -> WorkContext:
    defaults = dict(
        project="AI Desk",
        current_task="Owner handoff Phase 2",
        stage="implementation",
        possible_next_steps=("Draft the presence fusion tests", "Review the question generator"),
        unfinished_work=("Wire the Terminal renderer",),
        confidence=0.6,
        evidence=(_evidence("chrome_domain", "github.com"),),
        updated_at=NOW,
    )
    defaults.update(overrides)
    return WorkContext(**defaults)


class HandoffQuestionGeneratorNormalPathTest(unittest.TestCase):
    def test_generates_two_or_three_context_derived_options_plus_d(self) -> None:
        generator = HandoffQuestionGenerator(question_expiration_seconds=60, clock=lambda: NOW)
        context = _corroborated_context()
        question = generator.generate(context)

        self.assertIn("D", question.options)
        self.assertEqual(question.options["D"], "Do nothing")
        real_directions = [k for k in question.options if k != "D"]
        self.assertIn(len(real_directions), (2, 3))
        # Options must come only from possible_next_steps/unfinished_work.
        for letter in real_directions:
            text = question.options[letter]
            self.assertTrue(
                text in context.possible_next_steps
                or text.startswith("Continue: ")
            )

    def test_deterministic_ordering_and_ids(self) -> None:
        ids = iter(["question-fixed-1"])
        generator = HandoffQuestionGenerator(
            question_expiration_seconds=60,
            clock=lambda: NOW,
            question_id_factory=lambda: next(ids),
        )
        question = generator.generate(_corroborated_context())
        self.assertEqual(question.question_id, "question-fixed-1")
        self.assertEqual(list(question.options.keys())[-1], "D")
        self.assertEqual(question.created_at, NOW)
        self.assertEqual(question.expires_at, NOW + timedelta(seconds=60))

    def test_deduplicates_equivalent_options(self) -> None:
        generator = HandoffQuestionGenerator(question_expiration_seconds=60, clock=lambda: NOW)
        context = _corroborated_context(
            possible_next_steps=("Draft the tests", "  draft the tests  "),
            unfinished_work=(),
        )
        question = generator.generate(context)
        # Only one genuine direction after dedup -> not enough for a real
        # choice (needs at least 2) -> falls back to D-only.
        self.assertEqual(set(question.options.keys()), {"D"})


class HandoffQuestionGeneratorLowConfidenceTest(unittest.TestCase):
    def test_low_confidence_context_yields_d_only_question(self) -> None:
        generator = HandoffQuestionGenerator(question_expiration_seconds=60, clock=lambda: NOW)
        context = _corroborated_context(confidence=0.0)
        question = generator.generate(context)
        self.assertEqual(set(question.options.keys()), {"D"})

    def test_insufficient_direction_count_yields_d_only_question(self) -> None:
        generator = HandoffQuestionGenerator(question_expiration_seconds=60, clock=lambda: NOW)
        context = _corroborated_context(possible_next_steps=(), unfinished_work=())
        question = generator.generate(context)
        self.assertEqual(set(question.options.keys()), {"D"})


class HandoffQuestionGeneratorSanitizedContextTest(unittest.TestCase):
    def test_context_summary_never_contains_a_secret_or_full_url(self) -> None:
        # WorkContext itself rejects unsafe raw text at construction, so the
        # only way this could leak is if the generator concatenated fields
        # unsafely — assert the summary is exactly what redact_sensitive
        # would have allowed through.
        generator = HandoffQuestionGenerator(question_expiration_seconds=60, clock=lambda: NOW)
        context = _corroborated_context(project="AI Desk", current_task="Review PR")
        question = generator.generate(context)
        self.assertNotIn("https://", question.context_summary)
        self.assertNotIn("token=", question.context_summary)

    def test_never_claims_completed_work(self) -> None:
        generator = HandoffQuestionGenerator(question_expiration_seconds=60, clock=lambda: NOW)
        context = _corroborated_context()
        question = generator.generate(context)
        for text in question.options.values():
            self.assertNotIn("completed", text.lower())
            self.assertNotIn("finished", text.lower())


class TerminalRendererTest(unittest.TestCase):
    def test_full_question_is_rendered(self) -> None:
        generator = HandoffQuestionGenerator(question_expiration_seconds=60, clock=lambda: NOW)
        question = generator.generate(_corroborated_context())
        stream = io.StringIO()
        render_question(question, stream)
        output = stream.getvalue()

        self.assertIn(question.context_summary, output)
        self.assertIn(question.question, output)
        for letter, text in question.options.items():
            self.assertIn(text, output)
        self.assertIn(question.expires_at.isoformat(), output)
        self.assertIn(question.question_id, output)

    def test_wearable_payload_is_minimal(self) -> None:
        generator = HandoffQuestionGenerator(question_expiration_seconds=60, clock=lambda: NOW)
        question = generator.generate(_corroborated_context())
        question_id, letters = wearable_payload(question)
        self.assertEqual(question_id, question.question_id)
        self.assertEqual(set(letters), set(question.options.keys()))
        # No question text, context summary, or file/workspace content.
        self.assertNotIn(question.question, letters)
        self.assertNotIn(question.context_summary, letters)


class QuestionAuthorizationCancellationIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "owner_handoff.sqlite3"
        self.store = OwnerHandoffStore(self.db_path)
        self.store.create_task("task-1")
        # Drive the task to QUESTION_PENDING as a real cycle would.
        self.store.apply_transition(
            task_id="task-1", event_id="e1",
            from_state=S.OBSERVING, to_state=S.LEFT_CANDIDATE,
            reason="partial absence signal detected",
        )
        self.store.apply_transition(
            task_id="task-1", event_id="e2",
            from_state=S.LEFT_CANDIDATE, to_state=S.OWNER_LEFT_CONFIRMED,
            reason="10s confirmation window elapsed",
        )
        self.store.apply_transition(
            task_id="task-1", event_id="e3",
            from_state=S.OWNER_LEFT_CONFIRMED, to_state=S.QUESTION_PENDING,
            reason="handoff question generated",
        )
        self.generator = HandoffQuestionGenerator(
            question_expiration_seconds=60,
            clock=lambda: NOW,
            question_id_factory=lambda: "q-1",
        )
        self.question = self.generator.generate(_corroborated_context())
        self.lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        self.lifecycle.start_question(self.question)

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def _answer(self, button: WearableButton, **overrides) -> WearableAnswer:
        defaults = dict(
            device_id=DEVICE_ID,
            question_id=self.question.question_id,
            button=button,
            timestamp=NOW + timedelta(seconds=1),
        )
        defaults.update(overrides)
        return WearableAnswer(**defaults)

    def test_valid_selection_authorizes(self) -> None:
        answer = self._answer(WearableButton.A)
        result = self.lifecycle.submit_answer(answer, store=self.store, task_id="task-1")
        self.assertTrue(result.accepted)
        self.assertEqual(result.task_record.state, S.AUTHORIZED)

    def test_d_cancels(self) -> None:
        answer = self._answer(WearableButton.D)
        result = self.lifecycle.submit_answer(answer, store=self.store, task_id="task-1")
        self.assertTrue(result.accepted)
        self.assertEqual(result.task_record.state, S.CANCELED)

    def test_replaying_the_same_accepted_answer_is_idempotent(self) -> None:
        answer = self._answer(WearableButton.A)
        first = self.lifecycle.submit_answer(answer, store=self.store, task_id="task-1")
        history_len = len(self.store.history("task-1"))

        second = self.lifecycle.submit_answer(answer, store=self.store, task_id="task-1")
        self.assertEqual(second, first)
        self.assertEqual(len(self.store.history("task-1")), history_len)

    def test_a_different_answer_after_acceptance_is_rejected_as_duplicate(self) -> None:
        first_answer = self._answer(WearableButton.A)
        self.lifecycle.submit_answer(first_answer, store=self.store, task_id="task-1")

        different_answer = self._answer(WearableButton.D, timestamp=NOW + timedelta(seconds=2))
        result = self.lifecycle.submit_answer(
            different_answer, store=self.store, task_id="task-1"
        )
        self.assertFalse(result.accepted)
        self.assertEqual(result.rejection_reason, AnswerRejectionReason.DUPLICATE)
        self.assertEqual(self.store.get_task("task-1").state, S.AUTHORIZED)

    def test_evaluate_answer_result_cannot_authorize_anything(self) -> None:
        """A caller-constructed AnswerOutcome carries no authority -- there
        is no public function that accepts one and performs a transition."""

        answer = self._answer(WearableButton.A)
        outcome = self.lifecycle.evaluate_answer(answer)
        self.assertTrue(outcome.accepted)
        # The task is untouched by evaluate_answer -- only submit_answer can
        # move it, and it must be called explicitly with the raw answer.
        self.assertEqual(self.store.get_task("task-1").state, S.QUESTION_PENDING)

    def test_timeout_cancels_and_means_do_nothing(self) -> None:
        record = cancel_for_timeout(
            self.store, "task-1", self.question, now=NOW + timedelta(seconds=61)
        )
        self.assertEqual(record.state, S.CANCELED)
        history = self.store.history("task-1")
        self.assertEqual(history[-1].reason, "question expired")

    def test_timeout_before_expiry_is_a_no_op(self) -> None:
        record = cancel_for_timeout(
            self.store, "task-1", self.question, now=NOW + timedelta(seconds=5)
        )
        self.assertIsNone(record)
        self.assertEqual(self.store.get_task("task-1").state, S.QUESTION_PENDING)

    def test_owner_return_before_authorization_cancels(self) -> None:
        record = cancel_for_owner_return(self.store, "task-1", self.question)
        self.assertEqual(record.state, S.CANCELED)

    def test_wearable_disconnect_cancels(self) -> None:
        record = cancel_for_disconnect(self.store, "task-1", self.question)
        self.assertEqual(record.state, S.CANCELED)

    def test_invalid_answer_leaves_task_question_pending(self) -> None:
        bad_answer = self._answer(WearableButton.A, device_id="unknown-device")
        result = self.lifecycle.submit_answer(bad_answer, store=self.store, task_id="task-1")
        self.assertFalse(result.accepted)
        self.assertIsNone(result.task_record)
        self.assertEqual(self.store.get_task("task-1").state, S.QUESTION_PENDING)


if __name__ == "__main__":
    unittest.main()
