import tempfile
import unittest
from pathlib import Path

from application.owner_handoff.domain.presence import RadarState, WearableProximityState
from application.owner_handoff.fusion.leave_detector import (
    LeaveDetector,
    advance_state_machine_for_leave_evaluation,
)
from application.owner_handoff.fusion.return_detector import ReturnDetector
from application.owner_handoff.state_machine import OwnerHandoffState
from application.owner_handoff.store import OwnerHandoffStore

R = RadarState
W = WearableProximityState
S = OwnerHandoffState

IDLE_THRESHOLD = 5.0
CONFIRMATION_SECONDS = 10.0


class _FakeMonotonic:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _detector(monotonic: _FakeMonotonic | None = None, **overrides) -> LeaveDetector:
    return LeaveDetector(
        input_idle_threshold_seconds=overrides.pop("input_idle_threshold_seconds", IDLE_THRESHOLD),
        owner_leave_confirmation_seconds=overrides.pop(
            "owner_leave_confirmation_seconds", CONFIRMATION_SECONDS
        ),
        monotonic=monotonic or _FakeMonotonic(),
        **overrides,
    )


class LeaveDetectorNormalPathTest(unittest.TestCase):
    def test_unanimous_leave_confirms_after_idle_and_confirmation_windows(self) -> None:
        clock = _FakeMonotonic()
        detector = _detector(clock)
        detector.record_mouse_activity()
        detector.record_keyboard_activity()

        clock.advance(IDLE_THRESHOLD)
        first = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertTrue(first.candidate)
        self.assertFalse(first.confirmed)

        clock.advance(CONFIRMATION_SECONDS)
        second = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertTrue(second.confirmed)
        self.assertIsNotNone(second.confirmation_event_id)

    def test_effective_timing_is_idle_threshold_plus_confirmation_seconds(self) -> None:
        """5s idle + 10s confirmation = ~15s after the last input when
        radar/wearable are already absent. The detector is polled
        (evaluate() called each tick), as a real integration would — it has
        no way to retroactively notice a threshold crossing between polls."""

        clock = _FakeMonotonic()
        detector = _detector(clock)
        detector.record_mouse_activity()
        detector.record_keyboard_activity()

        # Idle threshold crossed: this tick opens the confirmation window.
        clock.advance(IDLE_THRESHOLD)
        opened = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertTrue(opened.candidate)
        self.assertFalse(opened.confirmed)

        clock.advance(CONFIRMATION_SECONDS - 0.001)
        not_yet = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertFalse(not_yet.confirmed)

        clock.advance(0.001)
        confirmed = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertTrue(confirmed.confirmed)


class LeaveDetectorBoundaryTest(unittest.TestCase):
    def test_does_not_confirm_at_9_999_seconds_confirms_at_10_0(self) -> None:
        clock = _FakeMonotonic()
        detector = _detector(clock)
        detector.record_mouse_activity()
        detector.record_keyboard_activity()
        clock.advance(IDLE_THRESHOLD)
        detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)  # opens window

        clock.advance(9.999)
        almost = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertFalse(almost.confirmed)

        clock.advance(0.001)
        exact = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertTrue(exact.confirmed)


class LeaveDetectorUnknownAndContradictionTest(unittest.TestCase):
    def test_unknown_radar_never_confirms(self) -> None:
        clock = _FakeMonotonic()
        detector = _detector(clock)
        clock.advance(IDLE_THRESHOLD + CONFIRMATION_SECONDS + 5)
        result = detector.evaluate(radar=R.UNKNOWN, wearable=W.OWNER_AWAY)
        self.assertFalse(result.candidate)
        self.assertFalse(result.confirmed)

    def test_unknown_wearable_never_confirms(self) -> None:
        clock = _FakeMonotonic()
        detector = _detector(clock)
        clock.advance(IDLE_THRESHOLD + CONFIRMATION_SECONDS + 5)
        result = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.UNKNOWN)
        self.assertFalse(result.confirmed)

    def test_radar_present_wearable_away_never_confirms(self) -> None:
        clock = _FakeMonotonic()
        detector = _detector(clock)
        clock.advance(IDLE_THRESHOLD + CONFIRMATION_SECONDS + 5)
        result = detector.evaluate(radar=R.PERSON_PRESENT, wearable=W.OWNER_AWAY)
        self.assertFalse(result.confirmed)

    def test_radar_absent_wearable_near_never_confirms(self) -> None:
        clock = _FakeMonotonic()
        detector = _detector(clock)
        clock.advance(IDLE_THRESHOLD + CONFIRMATION_SECONDS + 5)
        result = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_NEAR)
        self.assertFalse(result.confirmed)


class LeaveDetectorActiveInputTest(unittest.TestCase):
    def test_active_mouse_prevents_confirmation(self) -> None:
        clock = _FakeMonotonic()
        detector = _detector(clock)
        detector.record_mouse_activity()
        detector.record_keyboard_activity()
        clock.advance(IDLE_THRESHOLD + CONFIRMATION_SECONDS)
        # Fresh mouse activity right before evaluating keeps the window from
        # ever opening.
        detector.record_mouse_activity()
        result = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertFalse(result.candidate)

    def test_active_keyboard_prevents_confirmation(self) -> None:
        clock = _FakeMonotonic()
        detector = _detector(clock)
        detector.record_mouse_activity()
        detector.record_keyboard_activity()
        clock.advance(IDLE_THRESHOLD + CONFIRMATION_SECONDS)
        detector.record_keyboard_activity()
        result = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertFalse(result.candidate)


class LeaveDetectorSensorFlappingTest(unittest.TestCase):
    def test_flapping_radar_resets_the_continuous_timer(self) -> None:
        clock = _FakeMonotonic()
        detector = _detector(clock)
        detector.record_mouse_activity()
        detector.record_keyboard_activity()
        clock.advance(IDLE_THRESHOLD)
        detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)

        clock.advance(9)
        detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)  # 9s in, not yet confirmed

        # Radar flaps back to PRESENT momentarily.
        flap = detector.evaluate(radar=R.PERSON_PRESENT, wearable=W.OWNER_AWAY)
        self.assertTrue(flap.reverted)

        # Even though absence resumes immediately, the 10s timer must
        # restart from zero, not continue from 9s.
        resumed = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertTrue(resumed.candidate)
        self.assertFalse(resumed.confirmed)
        self.assertAlmostEqual(resumed.seconds_in_window, 0.0)

        clock.advance(9.999)
        still_not = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertFalse(still_not.confirmed)

        clock.advance(0.001)
        now_confirmed = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertTrue(now_confirmed.confirmed)


class LeaveDetectorDuplicateObservationTest(unittest.TestCase):
    def test_repeated_identical_observations_do_not_corrupt_state(self) -> None:
        clock = _FakeMonotonic()
        detector = _detector(clock)
        detector.record_mouse_activity()
        detector.record_keyboard_activity()
        clock.advance(IDLE_THRESHOLD)

        first = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        second = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        third = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)

        self.assertEqual(first.episode_id, second.episode_id)
        self.assertEqual(second.episode_id, third.episode_id)
        self.assertFalse(first.confirmed)
        self.assertFalse(third.confirmed)


class LeaveDetectorMonotonicRollbackTest(unittest.TestCase):
    def test_clock_rollback_fails_safe_never_confirms(self) -> None:
        clock = _FakeMonotonic()
        detector = _detector(clock)
        detector.record_mouse_activity()
        detector.record_keyboard_activity()
        clock.advance(IDLE_THRESHOLD)
        detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        clock.advance(9)
        detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)

        # Clock rolls backward (e.g. NTP correction on a misused monotonic
        # source). Even though naive math (rolled-back-now - condition_since)
        # could look like a huge elapsed time was skipped, this must never
        # confirm.
        clock.now -= 100.0
        rolled_back = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertFalse(rolled_back.confirmed)

        # Resuming forward motion must require a full fresh window, not
        # silently resume the old one.
        clock.now += 100.0 + IDLE_THRESHOLD
        resumed = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertFalse(resumed.confirmed)


class LeaveDetectorFreshStartTest(unittest.TestCase):
    """Repair: a newly constructed detector must not treat the mouse/
    keyboard as idle since the beginning of time -- it must still require
    the full input_idle_threshold_seconds to elapse from construction."""

    def test_fresh_detector_with_radar_and_wearable_already_absent_still_waits(
        self,
    ) -> None:
        clock = _FakeMonotonic()
        detector = _detector(clock)  # no record_*_activity() ever called

        immediately = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertFalse(immediately.candidate)

        clock.advance(IDLE_THRESHOLD - 0.001)
        still_not_idle = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertFalse(still_not_idle.candidate)

        clock.advance(0.001)
        now_idle = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertTrue(now_idle.candidate)
        self.assertFalse(now_idle.confirmed)

        clock.advance(CONFIRMATION_SECONDS)
        confirmed = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertTrue(confirmed.confirmed)

    def test_explicit_initial_activity_timestamp_is_honored(self) -> None:
        clock = _FakeMonotonic()
        clock.now = 100.0
        detector = _detector(clock, initial_activity_at=90.0)  # already 10s idle
        # Only 10s (not the full IDLE_THRESHOLD from *now*) need elapse
        # further before mouse/keyboard read as idle, since the caller
        # already told us the real last-input time.
        result = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertTrue(result.candidate)


class LeaveDetectorOneShotConfirmationTest(unittest.TestCase):
    def test_only_the_first_crossing_tick_reports_confirmed(self) -> None:
        clock = _FakeMonotonic()
        detector = _detector(clock)
        detector.record_mouse_activity()
        detector.record_keyboard_activity()
        clock.advance(IDLE_THRESHOLD)
        detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        clock.advance(CONFIRMATION_SECONDS)

        first = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertTrue(first.confirmed)

        clock.advance(5.0)
        second = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertFalse(second.confirmed)

        clock.advance(100.0)
        third = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertFalse(third.confirmed)


class LeaveDetectorSecondLeaveAfterReturnTest(unittest.TestCase):
    def test_second_leave_after_reset_produces_a_new_event_id(self) -> None:
        clock = _FakeMonotonic()
        detector = _detector(clock)
        detector.record_mouse_activity()
        detector.record_keyboard_activity()
        clock.advance(IDLE_THRESHOLD)
        detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)  # opens window
        clock.advance(CONFIRMATION_SECONDS)
        first = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertTrue(first.confirmed)

        detector.reset()  # simulated confirmed return
        detector.record_mouse_activity()
        detector.record_keyboard_activity()
        clock.advance(IDLE_THRESHOLD)
        detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)  # opens new window
        clock.advance(CONFIRMATION_SECONDS)
        second = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)

        self.assertTrue(second.confirmed)
        self.assertNotEqual(first.confirmation_event_id, second.confirmation_event_id)


class LeaveDetectorStateMachineIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "owner_handoff.sqlite3"
        self.store = OwnerHandoffStore(self.db_path)
        self.store.create_task("task-1")

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def test_one_confirmation_event_cannot_authorize_two_task_transitions(self) -> None:
        clock = _FakeMonotonic()
        detector = _detector(clock)
        detector.record_mouse_activity()
        detector.record_keyboard_activity()
        clock.advance(IDLE_THRESHOLD)
        detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)  # opens window
        clock.advance(CONFIRMATION_SECONDS)

        first = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertTrue(first.confirmed)
        advance_state_machine_for_leave_evaluation(self.store, "task-1", first)
        self.assertEqual(
            self.store.get_task("task-1").state, S.OWNER_LEFT_CONFIRMED
        )
        history_after_first = len(self.store.history("task-1"))

        # Repair: the detector itself is now one-shot -- a later tick of the
        # SAME episode never reports confirmed=True again, even though the
        # window has objectively been open long enough.
        second = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        self.assertFalse(second.confirmed)
        self.assertIsNone(second.confirmation_event_id)
        advance_state_machine_for_leave_evaluation(self.store, "task-1", second)
        self.assertEqual(len(self.store.history("task-1")), history_after_first)
        self.assertEqual(
            self.store.get_task("task-1").state, S.OWNER_LEFT_CONFIRMED
        )

        # Defense in depth: even if a caller somehow replayed the FIRST
        # (already-applied) evaluation object again, the store's own
        # event-id idempotency must still prevent a second transition.
        advance_state_machine_for_leave_evaluation(self.store, "task-1", first)
        self.assertEqual(len(self.store.history("task-1")), history_after_first)
        self.assertEqual(
            self.store.get_task("task-1").state, S.OWNER_LEFT_CONFIRMED
        )

    def test_full_candidate_to_confirmed_drive(self) -> None:
        clock = _FakeMonotonic()
        detector = _detector(clock)
        detector.record_mouse_activity()
        detector.record_keyboard_activity()

        clock.advance(IDLE_THRESHOLD)
        candidate = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        advance_state_machine_for_leave_evaluation(self.store, "task-1", candidate)
        self.assertEqual(self.store.get_task("task-1").state, S.LEFT_CANDIDATE)

        clock.advance(CONFIRMATION_SECONDS)
        confirmed = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        advance_state_machine_for_leave_evaluation(self.store, "task-1", confirmed)
        self.assertEqual(
            self.store.get_task("task-1").state, S.OWNER_LEFT_CONFIRMED
        )

    def test_reverted_candidate_returns_task_to_observing(self) -> None:
        clock = _FakeMonotonic()
        detector = _detector(clock)
        detector.record_mouse_activity()
        detector.record_keyboard_activity()
        clock.advance(IDLE_THRESHOLD)
        candidate = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_AWAY)
        advance_state_machine_for_leave_evaluation(self.store, "task-1", candidate)
        self.assertEqual(self.store.get_task("task-1").state, S.LEFT_CANDIDATE)

        reverted = detector.evaluate(radar=R.PERSON_PRESENT, wearable=W.OWNER_AWAY)
        advance_state_machine_for_leave_evaluation(self.store, "task-1", reverted)
        self.assertEqual(self.store.get_task("task-1").state, S.OBSERVING)


class ReturnDetectorNormalPathTest(unittest.TestCase):
    def test_present_and_near_confirms(self) -> None:
        detector = ReturnDetector()
        result = detector.evaluate(radar=R.PERSON_PRESENT, wearable=W.OWNER_NEAR)
        self.assertTrue(result.confirmed)
        self.assertIsNotNone(result.event_id)


class ReturnDetectorRejectionTest(unittest.TestCase):
    def test_present_and_away_does_not_confirm(self) -> None:
        detector = ReturnDetector()
        result = detector.evaluate(radar=R.PERSON_PRESENT, wearable=W.OWNER_AWAY)
        self.assertFalse(result.confirmed)

    def test_absent_and_near_does_not_confirm(self) -> None:
        detector = ReturnDetector()
        result = detector.evaluate(radar=R.PERSON_ABSENT, wearable=W.OWNER_NEAR)
        self.assertFalse(result.confirmed)

    def test_unknown_never_confirms(self) -> None:
        detector = ReturnDetector()
        self.assertFalse(
            detector.evaluate(radar=R.UNKNOWN, wearable=W.OWNER_NEAR).confirmed
        )
        self.assertFalse(
            detector.evaluate(radar=R.PERSON_PRESENT, wearable=W.UNKNOWN).confirmed
        )

    def test_supporting_evidence_never_overrides_contradictory_hardware(self) -> None:
        detector = ReturnDetector()
        result = detector.evaluate(
            radar=R.PERSON_ABSENT,
            wearable=W.UNKNOWN,
            mouse_active=True,
            keyboard_active=True,
        )
        self.assertFalse(result.confirmed)
        self.assertTrue(result.supporting_evidence)


class ReturnDetectorRepeatedSamplesTest(unittest.TestCase):
    def test_repeated_samples_emit_once(self) -> None:
        detector = ReturnDetector()
        first = detector.evaluate(radar=R.PERSON_PRESENT, wearable=W.OWNER_NEAR)
        second = detector.evaluate(radar=R.PERSON_PRESENT, wearable=W.OWNER_NEAR)
        third = detector.evaluate(radar=R.PERSON_PRESENT, wearable=W.OWNER_NEAR)
        self.assertTrue(first.confirmed)
        self.assertFalse(second.confirmed)
        self.assertFalse(third.confirmed)

    def test_reset_allows_a_later_confirmation_with_a_new_event_id(self) -> None:
        detector = ReturnDetector()
        first = detector.evaluate(radar=R.PERSON_PRESENT, wearable=W.OWNER_NEAR)
        detector.reset()
        second = detector.evaluate(radar=R.PERSON_PRESENT, wearable=W.OWNER_NEAR)
        self.assertTrue(first.confirmed)
        self.assertTrue(second.confirmed)
        self.assertNotEqual(first.event_id, second.event_id)


if __name__ == "__main__":
    unittest.main()
