import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from application.owner_handoff.adapters.radar import SimulatorRadar
from application.owner_handoff.adapters.wearable import WearableSimulator, WearableTransportError
from application.owner_handoff.domain.presence import (
    RadarState,
    WearableAnswer,
    WearableButton,
    WearableProximityState,
)
from application.owner_handoff.domain.question import (
    CODEX_DATA_PROMPT,
    HandoffQuestion,
    PermissionQuestion,
)
from application.owner_handoff.questions.lifecycle import (
    AnswerRejectionReason,
    QuestionAlreadyPendingError,
    QuestionKind,
    QuestionLifecycle,
)
from application.owner_handoff.questions.terminal import wearable_payload
from application.owner_handoff.state_machine import OwnerHandoffState, PermissionKind
from application.owner_handoff.store import OwnerHandoffStore

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
DEVICE_ID = "wearable-001"


def _question(**overrides) -> HandoffQuestion:
    defaults = dict(
        question_id="q-1",
        context_summary="AI Desk: draft PR (review)",
        question="What should AI Desk do?",
        options={"A": "Continue draft PR", "B": "Research related issue", "D": "Do nothing"},
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=60),
    )
    defaults.update(overrides)
    return HandoffQuestion(**defaults)


def _answer(**overrides) -> WearableAnswer:
    defaults = dict(
        device_id=DEVICE_ID,
        question_id="q-1",
        button=WearableButton.A,
        timestamp=NOW + timedelta(seconds=5),
    )
    defaults.update(overrides)
    return WearableAnswer(**defaults)


class RadarSimulatorTest(unittest.TestCase):
    def test_set_state_and_read_are_deterministic(self) -> None:
        radar = SimulatorRadar(clock=lambda: NOW)
        radar.set_state(RadarState.PERSON_ABSENT)
        sample = radar.read()
        self.assertEqual(sample.state, RadarState.PERSON_ABSENT)
        self.assertEqual(sample.observed_at, NOW)

    def test_unknown_default_state(self) -> None:
        radar = SimulatorRadar(clock=lambda: NOW)
        self.assertEqual(radar.read().state, RadarState.UNKNOWN)

    def test_rejects_non_enum_state(self) -> None:
        radar = SimulatorRadar(clock=lambda: NOW)
        with self.assertRaises(TypeError):
            radar.set_state("PERSON_ABSENT")  # type: ignore[arg-type]


class WearableSimulatorProximityTest(unittest.TestCase):
    def test_set_proximity_and_read(self) -> None:
        wearable = WearableSimulator(DEVICE_ID, clock=lambda: NOW)
        wearable.set_proximity(WearableProximityState.OWNER_NEAR)
        sample = wearable.read_proximity()
        self.assertEqual(sample.state, WearableProximityState.OWNER_NEAR)
        self.assertEqual(sample.device_id, DEVICE_ID)


class WearableSimulatorDisconnectRegressionTest(unittest.TestCase):
    """Repair: disconnected wearable behavior must fail closed."""

    def test_disconnected_proximity_reads_unknown_never_stale_away(self) -> None:
        wearable = WearableSimulator(DEVICE_ID, clock=lambda: NOW)
        wearable.set_proximity(WearableProximityState.OWNER_AWAY)
        wearable.disconnect()
        sample = wearable.read_proximity()
        self.assertEqual(sample.state, WearableProximityState.UNKNOWN)

    def test_queued_answer_before_disconnect_is_discarded_not_delivered_after_reconnect(
        self,
    ) -> None:
        wearable = WearableSimulator(DEVICE_ID, clock=lambda: NOW)
        wearable.send_question("q-1", ("A", "B", "D"))
        wearable.queue_answer(WearableButton.A)
        wearable.disconnect()
        wearable.reconnect()
        self.assertIsNone(wearable.poll_answer())

    def test_poll_answer_returns_none_while_disconnected(self) -> None:
        wearable = WearableSimulator(DEVICE_ID, clock=lambda: NOW)
        wearable.send_question("q-1", ("A", "B", "D"))
        wearable.queue_answer(WearableButton.A)
        wearable.disconnect()
        self.assertIsNone(wearable.poll_answer())


class WearableSimulatorLetterValidationTest(unittest.TestCase):
    def test_empty_letters_rejected(self) -> None:
        wearable = WearableSimulator(DEVICE_ID, clock=lambda: NOW)
        with self.assertRaises(ValueError):
            wearable.send_question("q-1", ())

    def test_duplicate_letters_rejected(self) -> None:
        wearable = WearableSimulator(DEVICE_ID, clock=lambda: NOW)
        with self.assertRaises(ValueError):
            wearable.send_question("q-1", ("A", "A", "D"))

    def test_mixed_family_rejected(self) -> None:
        wearable = WearableSimulator(DEVICE_ID, clock=lambda: NOW)
        with self.assertRaises(ValueError):
            wearable.send_question("q-1", ("A", "YES"))

    def test_arbitrary_strings_rejected(self) -> None:
        wearable = WearableSimulator(DEVICE_ID, clock=lambda: NOW)
        with self.assertRaises(ValueError):
            wearable.send_question("q-1", ("A", "MAYBE"))

    def test_pure_permission_letters_accepted(self) -> None:
        wearable = WearableSimulator(DEVICE_ID, clock=lambda: NOW)
        wearable.send_question("q-2", ("YES", "NO"))
        self.assertEqual(wearable.last_sent_payload["letters"], ("YES", "NO"))


class WearableSimulatorMinimalPayloadTest(unittest.TestCase):
    def test_only_question_id_and_letters_are_sent(self) -> None:
        question = _question()
        wearable = WearableSimulator(DEVICE_ID, clock=lambda: NOW)
        question_id, letters = wearable_payload(question)
        wearable.send_question(question_id, letters)

        payload = wearable.last_sent_payload
        self.assertEqual(set(payload.keys()), {"question_id", "letters"})
        self.assertEqual(payload["question_id"], "q-1")
        self.assertEqual(payload["letters"], ("A", "B", "D"))
        # The full question text/context must never appear anywhere in the
        # payload that was sent.
        self.assertNotIn("draft PR", str(payload))
        self.assertNotIn("What should AI Desk do?", str(payload))

    def test_send_question_raises_when_disconnected(self) -> None:
        wearable = WearableSimulator(DEVICE_ID, clock=lambda: NOW)
        wearable.disconnect()
        with self.assertRaises(WearableTransportError):
            wearable.send_question("q-1", ("A", "D"))


class WearableAnswerMalformedPacketTest(unittest.TestCase):
    """Malformed packets are rejected at construction — the domain
    boundary — never persisted, never interpreted."""

    def test_empty_device_id_rejected(self) -> None:
        with self.assertRaises(ValueError):
            WearableAnswer(
                device_id="",
                question_id="q-1",
                button=WearableButton.A,
                timestamp=NOW,
            )

    def test_naive_timestamp_rejected(self) -> None:
        with self.assertRaises(ValueError):
            WearableAnswer(
                device_id=DEVICE_ID,
                question_id="q-1",
                button=WearableButton.A,
                timestamp=datetime(2026, 7, 25, 12, 0),
            )

    def test_non_enum_button_rejected(self) -> None:
        with self.assertRaises(TypeError):
            WearableAnswer(
                device_id=DEVICE_ID,
                question_id="q-1",
                button="Z",  # type: ignore[arg-type]
                timestamp=NOW,
            )


class QuestionLifecycleAllowlistTest(unittest.TestCase):
    def test_allowlisted_device_is_accepted(self) -> None:
        lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        lifecycle.start_question(_question())
        outcome = lifecycle.evaluate_answer(_answer())
        self.assertTrue(outcome.accepted)

    def test_empty_allowlist_rejects_every_answer(self) -> None:
        lifecycle = QuestionLifecycle(device_allowlist=(), clock=lambda: NOW)
        lifecycle.start_question(_question())
        outcome = lifecycle.evaluate_answer(_answer())
        self.assertFalse(outcome.accepted)
        self.assertEqual(outcome.rejection_reason, AnswerRejectionReason.UNKNOWN_DEVICE)

    def test_unknown_device_rejected(self) -> None:
        lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        lifecycle.start_question(_question())
        outcome = lifecycle.evaluate_answer(_answer(device_id="some-other-wearable"))
        self.assertEqual(outcome.rejection_reason, AnswerRejectionReason.UNKNOWN_DEVICE)


class QuestionLifecycleRejectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)

    def test_wrong_question_id_rejected(self) -> None:
        self.lifecycle.start_question(_question())
        outcome = self.lifecycle.evaluate_answer(_answer(question_id="stale-question"))
        self.assertEqual(outcome.rejection_reason, AnswerRejectionReason.WRONG_QUESTION_ID)

    def test_duplicate_answer_rejected(self) -> None:
        """Duplicate detection is authoritative-path (submit_answer) state,
        not something the side-effect-free evaluate_answer tracks."""

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = OwnerHandoffStore(Path(tmp_dir) / "owner_handoff.sqlite3")
            store.create_task("task-1")
            store.apply_transition(
                task_id="task-1", event_id="e1",
                from_state=OwnerHandoffState.OBSERVING,
                to_state=OwnerHandoffState.LEFT_CANDIDATE, reason="r1",
            )
            store.apply_transition(
                task_id="task-1", event_id="e2",
                from_state=OwnerHandoffState.LEFT_CANDIDATE,
                to_state=OwnerHandoffState.OWNER_LEFT_CONFIRMED, reason="r2",
            )
            store.apply_transition(
                task_id="task-1", event_id="e3",
                from_state=OwnerHandoffState.OWNER_LEFT_CONFIRMED,
                to_state=OwnerHandoffState.QUESTION_PENDING, reason="r3",
            )

            self.lifecycle.start_question(_question())
            first = self.lifecycle.submit_answer(_answer(), store=store, task_id="task-1")
            self.assertTrue(first.accepted)
            second = self.lifecycle.submit_answer(
                _answer(button=WearableButton.B), store=store, task_id="task-1"
            )
            self.assertFalse(second.accepted)
            self.assertEqual(second.rejection_reason, AnswerRejectionReason.DUPLICATE)
            store.close()

    def test_expired_question_rejected(self) -> None:
        lifecycle = QuestionLifecycle(
            device_allowlist=(DEVICE_ID,), clock=lambda: NOW + timedelta(seconds=61)
        )
        lifecycle.start_question(_question())
        outcome = lifecycle.evaluate_answer(_answer(timestamp=NOW + timedelta(seconds=61)))
        self.assertEqual(outcome.rejection_reason, AnswerRejectionReason.EXPIRED)

    def test_answer_timestamped_after_expiration_rejected(self) -> None:
        self.lifecycle.start_question(_question())
        outcome = self.lifecycle.evaluate_answer(
            _answer(timestamp=NOW + timedelta(seconds=61))
        )
        self.assertEqual(outcome.rejection_reason, AnswerRejectionReason.EXPIRED)

    def test_answer_timestamped_before_creation_rejected(self) -> None:
        self.lifecycle.start_question(_question())
        outcome = self.lifecycle.evaluate_answer(
            _answer(timestamp=NOW - timedelta(seconds=1))
        )
        self.assertEqual(outcome.rejection_reason, AnswerRejectionReason.BEFORE_CREATION)

    def test_wrong_state_rejected_when_nothing_pending(self) -> None:
        outcome = self.lifecycle.evaluate_answer(_answer())
        self.assertEqual(outcome.rejection_reason, AnswerRejectionReason.WRONG_STATE)

    def test_handoff_selection_rejects_yes_no(self) -> None:
        self.lifecycle.start_question(_question(), kind=QuestionKind.HANDOFF_SELECTION)
        outcome = self.lifecycle.evaluate_answer(_answer(button=WearableButton.YES))
        self.assertEqual(
            outcome.rejection_reason, AnswerRejectionReason.INVALID_BUTTON_FAMILY
        )

    def test_permission_question_rejects_abcd(self) -> None:
        permission_question = _question(
            question_id="q-2",
            options={"D": "Do nothing"},
        )
        self.lifecycle.start_question(permission_question, kind=QuestionKind.PERMISSION)
        outcome = self.lifecycle.evaluate_answer(
            _answer(question_id="q-2", button=WearableButton.A)
        )
        self.assertEqual(
            outcome.rejection_reason, AnswerRejectionReason.INVALID_BUTTON_FAMILY
        )

    def test_option_not_offered_rejected(self) -> None:
        two_direction_question = _question(
            options={
                "A": "Continue draft PR",
                "B": "Research related issue",
                "D": "Do nothing",
            }
        )
        self.lifecycle.start_question(two_direction_question)
        # C is a valid handoff-selection button in general, but this
        # question only offers A/B/D.
        outcome = self.lifecycle.evaluate_answer(_answer(button=WearableButton.C))
        self.assertEqual(
            outcome.rejection_reason, AnswerRejectionReason.OPTION_NOT_OFFERED
        )

    def test_disconnect_rejects_every_answer(self) -> None:
        self.lifecycle.start_question(_question())
        self.lifecycle.disconnect()
        outcome = self.lifecycle.evaluate_answer(_answer())
        self.assertEqual(outcome.rejection_reason, AnswerRejectionReason.DISCONNECTED)

    def test_future_timestamp_beyond_tolerance_rejected(self) -> None:
        self.lifecycle.start_question(_question())
        outcome = self.lifecycle.evaluate_answer(
            _answer(timestamp=NOW + timedelta(seconds=30))
        )
        self.assertEqual(outcome.rejection_reason, AnswerRejectionReason.FUTURE_TIMESTAMP)

    def test_timestamp_within_clock_skew_tolerance_accepted(self) -> None:
        self.lifecycle.start_question(_question())
        # NOW is the fixed clock; a couple of seconds "in the future" is
        # within the documented small tolerance.
        outcome = self.lifecycle.evaluate_answer(
            _answer(timestamp=NOW + timedelta(seconds=2))
        )
        self.assertTrue(outcome.accepted)


class QuestionLifecycleStartQuestionOverwriteTest(unittest.TestCase):
    """Repair: start_question must reject overwriting a still-pending,
    unexpired question."""

    def test_overwriting_a_still_pending_question_raises(self) -> None:
        lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        lifecycle.start_question(_question())
        with self.assertRaises(QuestionAlreadyPendingError):
            lifecycle.start_question(_question(question_id="q-2"))

    def test_starting_a_new_question_after_explicit_cancel_succeeds(self) -> None:
        lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        lifecycle.start_question(_question())
        lifecycle.cancel_pending()
        lifecycle.start_question(_question(question_id="q-2"))
        self.assertEqual(lifecycle.pending_question.question_id, "q-2")

    def test_starting_a_new_question_after_expiry_succeeds(self) -> None:
        clock = {"now": NOW}
        lifecycle = QuestionLifecycle(
            device_allowlist=(DEVICE_ID,), clock=lambda: clock["now"]
        )
        lifecycle.start_question(_question())
        clock["now"] = NOW + timedelta(seconds=61)
        lifecycle.start_question(_question(question_id="q-2"))
        self.assertEqual(lifecycle.pending_question.question_id, "q-2")


class QuestionLifecycleRetrySafetyTest(unittest.TestCase):
    """Repair: an answer is consumed only after the transition succeeds; a
    failed transition must remain retryable."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = OwnerHandoffStore(Path(self.temp_dir.name) / "owner_handoff.sqlite3")
        self.store.create_task("task-1")

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def test_failed_transition_does_not_consume_the_answer_and_retry_succeeds(
        self,
    ) -> None:
        lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        lifecycle.start_question(_question())
        answer = _answer()

        # The task is still at OBSERVING, not QUESTION_PENDING -- the store
        # transition itself must fail (StaleTransitionError).
        with self.assertRaises(Exception):
            lifecycle.submit_answer(answer, store=self.store, task_id="task-1")

        # The answer must not have been marked consumed by the failed
        # attempt: driving the task to QUESTION_PENDING for real and
        # retrying the identical answer must still succeed.
        self.store.apply_transition(
            task_id="task-1", event_id="e1",
            from_state=OwnerHandoffState.OBSERVING,
            to_state=OwnerHandoffState.LEFT_CANDIDATE, reason="r1",
        )
        self.store.apply_transition(
            task_id="task-1", event_id="e2",
            from_state=OwnerHandoffState.LEFT_CANDIDATE,
            to_state=OwnerHandoffState.OWNER_LEFT_CONFIRMED, reason="r2",
        )
        self.store.apply_transition(
            task_id="task-1", event_id="e3",
            from_state=OwnerHandoffState.OWNER_LEFT_CONFIRMED,
            to_state=OwnerHandoffState.QUESTION_PENDING, reason="r3",
        )
        result = lifecycle.submit_answer(answer, store=self.store, task_id="task-1")
        self.assertTrue(result.accepted)
        self.assertEqual(result.task_record.state, OwnerHandoffState.AUTHORIZED)

    def test_rejected_answer_never_mutates_lifecycle_or_task_state(self) -> None:
        self.store.apply_transition(
            task_id="task-1", event_id="e1",
            from_state=OwnerHandoffState.OBSERVING,
            to_state=OwnerHandoffState.LEFT_CANDIDATE, reason="r1",
        )
        self.store.apply_transition(
            task_id="task-1", event_id="e2",
            from_state=OwnerHandoffState.LEFT_CANDIDATE,
            to_state=OwnerHandoffState.OWNER_LEFT_CONFIRMED, reason="r2",
        )
        self.store.apply_transition(
            task_id="task-1", event_id="e3",
            from_state=OwnerHandoffState.OWNER_LEFT_CONFIRMED,
            to_state=OwnerHandoffState.QUESTION_PENDING, reason="r3",
        )
        lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        lifecycle.start_question(_question())

        bad_answer = _answer(device_id="unknown-device")
        result = lifecycle.submit_answer(bad_answer, store=self.store, task_id="task-1")
        self.assertFalse(result.accepted)
        self.assertEqual(self.store.get_task("task-1").state, OwnerHandoffState.QUESTION_PENDING)
        history_len = len(self.store.history("task-1"))

        # A subsequent valid answer must still work -- the rejection above
        # left nothing consumed.
        good_result = lifecycle.submit_answer(_answer(), store=self.store, task_id="task-1")
        self.assertTrue(good_result.accepted)
        self.assertGreater(len(self.store.history("task-1")), history_len)


class PermissionQuestionTest(unittest.TestCase):
    """Repair: permission questions are a distinct, real domain type."""

    def test_codex_data_question_constructs_with_yes_no_options(self) -> None:
        question = PermissionQuestion(
            question_id="perm-1",
            permission_kind=PermissionKind.CODEX_DATA,
            context_summary="AI Desk: draft PR",
            prompt=CODEX_DATA_PROMPT,
            created_at=NOW,
            expires_at=NOW + timedelta(seconds=60),
            duplicate_path="/tmp/session/duplicate",
        )
        self.assertEqual(dict(question.options), {"YES": "Yes", "NO": "No"})

    def test_package_install_question_requires_packages_and_argv(self) -> None:
        with self.assertRaises(ValueError):
            PermissionQuestion(
                question_id="perm-2",
                permission_kind=PermissionKind.PACKAGE_INSTALL,
                context_summary="AI Desk: draft PR",
                prompt="Install requests==2.31.0?",
                created_at=NOW,
                expires_at=NOW + timedelta(seconds=60),
            )

    def test_package_install_question_with_packages_and_argv_constructs(self) -> None:
        question = PermissionQuestion(
            question_id="perm-2",
            permission_kind=PermissionKind.PACKAGE_INSTALL,
            context_summary="AI Desk: draft PR",
            prompt="Install requests==2.31.0?",
            created_at=NOW,
            expires_at=NOW + timedelta(seconds=60),
            duplicate_path="/tmp/session/duplicate",
            package_names=("requests==2.31.0",),
            proposed_argv=("python", "-m", "pip", "install", "requests==2.31.0"),
        )
        self.assertEqual(question.package_names, ("requests==2.31.0",))

    def test_codex_data_question_rejects_package_fields(self) -> None:
        with self.assertRaises(ValueError):
            PermissionQuestion(
                question_id="perm-3",
                permission_kind=PermissionKind.CODEX_DATA,
                context_summary="AI Desk: draft PR",
                prompt=CODEX_DATA_PROMPT,
                created_at=NOW,
                expires_at=NOW + timedelta(seconds=60),
                package_names=("requests",),
            )

    def test_unsafe_prompt_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PermissionQuestion(
                question_id="perm-4",
                permission_kind=PermissionKind.CODEX_DATA,
                context_summary="AI Desk: draft PR",
                prompt="Allow? https://example.com/x?token=SECRET",
                created_at=NOW,
                expires_at=NOW + timedelta(seconds=60),
            )

    def test_unsafe_package_name_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PermissionQuestion(
                question_id="perm-5",
                permission_kind=PermissionKind.PACKAGE_INSTALL,
                context_summary="AI Desk: draft PR",
                prompt="Install?",
                created_at=NOW,
                expires_at=NOW + timedelta(seconds=60),
                package_names=("https://evil.example/pkg",),
                proposed_argv=("python", "-m", "pip", "install", "https://evil.example/pkg"),
            )


if __name__ == "__main__":
    unittest.main()
