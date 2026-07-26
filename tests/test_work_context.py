import unittest
from datetime import datetime, timedelta, timezone

from application.owner_handoff.domain.work_context import (
    Evidence,
    EvidenceKind,
    WorkContext,
    WorkContextTracker,
    redact_sensitive,
)

START = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


class _FakeClock:
    def __init__(self, start: datetime) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current = self.current + timedelta(seconds=seconds)


class WorkContextValidationTest(unittest.TestCase):
    def test_matches_master_spec_contract_shape(self) -> None:
        context = WorkContext(updated_at=START)
        payload = context.as_dict()
        self.assertEqual(
            set(payload.keys()),
            {
                "project",
                "current_task",
                "stage",
                "recent_actions",
                "recent_files",
                "unfinished_work",
                "possible_next_steps",
                "confidence",
                "evidence",
                "updated_at",
            },
        )

    def test_naive_timestamp_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            WorkContext(updated_at=datetime(2026, 7, 25, 12, 0))

    def test_deterministic_serialization(self) -> None:
        evidence = (
            Evidence(
                kind=EvidenceKind.OBSERVATION,
                source="chrome_domain",
                detail="github.com",
                observed_at=START,
            ),
        )
        context_a = WorkContext(evidence=evidence, updated_at=START)
        context_b = WorkContext(evidence=evidence, updated_at=START)
        self.assertEqual(context_a.as_dict(), context_b.as_dict())


class WorkContextTrackerDeduplicationTest(unittest.TestCase):
    def test_recording_the_same_evidence_twice_corroborates_not_duplicates(self) -> None:
        clock = _FakeClock(START)
        tracker = WorkContextTracker(clock=clock)
        tracker.record_evidence(EvidenceKind.OBSERVATION, "chrome_domain", "github.com")
        tracker.record_evidence(EvidenceKind.OBSERVATION, "chrome_domain", "github.com")
        self.assertEqual(len(tracker.context.evidence), 1)
        self.assertEqual(tracker.context.evidence[0].corroboration_count, 2)


class WorkContextTrackerBoundedListsTest(unittest.TestCase):
    def test_evidence_list_is_bounded(self) -> None:
        clock = _FakeClock(START)
        tracker = WorkContextTracker(clock=clock)
        for i in range(WorkContextTracker.MAX_EVIDENCE_ITEMS + 5):
            tracker.record_evidence(EvidenceKind.OBSERVATION, "window_title", f"task-{i}")
        self.assertEqual(
            len(tracker.context.evidence), WorkContextTracker.MAX_EVIDENCE_ITEMS
        )

    def test_recent_actions_list_is_bounded(self) -> None:
        clock = _FakeClock(START)
        tracker = WorkContextTracker(clock=clock)
        for i in range(WorkContextTracker.MAX_LIST_ITEMS + 5):
            tracker.append_recent_action(f"action-{i}")
        self.assertEqual(
            len(tracker.context.recent_actions), WorkContextTracker.MAX_LIST_ITEMS
        )


class WorkContextTrackerConfidenceIncreaseTest(unittest.TestCase):
    def test_confidence_rises_only_after_corroboration(self) -> None:
        clock = _FakeClock(START)
        tracker = WorkContextTracker(clock=clock)
        tracker.record_evidence(EvidenceKind.OBSERVATION, "chrome_domain", "github.com")
        self.assertEqual(tracker.context.confidence, 0.0)
        tracker.record_evidence(EvidenceKind.OBSERVATION, "chrome_domain", "github.com")
        self.assertGreater(tracker.context.confidence, 0.0)

    def test_single_keyword_never_sets_project(self) -> None:
        clock = _FakeClock(START)
        tracker = WorkContextTracker(clock=clock)
        tracker.record_evidence(EvidenceKind.OBSERVATION, "chrome_domain", "github.com")
        applied = tracker.set_project_and_task(
            project="AI Desk",
            current_task="Review PR",
            evidence_source="chrome_domain",
            evidence_detail="github.com",
        )
        self.assertFalse(applied)
        self.assertEqual(tracker.context.project, "")

    def test_corroborated_evidence_allows_setting_project(self) -> None:
        clock = _FakeClock(START)
        tracker = WorkContextTracker(clock=clock)
        tracker.record_evidence(EvidenceKind.OBSERVATION, "chrome_domain", "github.com")
        tracker.record_evidence(EvidenceKind.OBSERVATION, "chrome_domain", "github.com")
        applied = tracker.set_project_and_task(
            project="AI Desk",
            current_task="Review PR",
            evidence_source="chrome_domain",
            evidence_detail="github.com",
        )
        self.assertTrue(applied)
        self.assertEqual(tracker.context.project, "AI Desk")


class WorkContextTrackerConfidenceDecreaseTest(unittest.TestCase):
    def test_conflict_lowers_confidence(self) -> None:
        clock = _FakeClock(START)
        tracker = WorkContextTracker(clock=clock)
        tracker.record_evidence(EvidenceKind.OBSERVATION, "chrome_domain", "github.com")
        tracker.record_evidence(EvidenceKind.OBSERVATION, "chrome_domain", "github.com")
        confidence_before = tracker.context.confidence
        tracker.register_conflict()
        self.assertLess(tracker.context.confidence, confidence_before)

    def test_stale_evidence_lowers_confidence(self) -> None:
        clock = _FakeClock(START)
        tracker = WorkContextTracker(clock=clock)
        tracker.record_evidence(EvidenceKind.OBSERVATION, "chrome_domain", "github.com")
        tracker.record_evidence(EvidenceKind.OBSERVATION, "chrome_domain", "github.com")
        confidence_before = tracker.context.confidence
        clock.advance(WorkContextTracker.STALE_AFTER_SECONDS + 1)
        tracker.apply_staleness()
        self.assertLess(tracker.context.confidence, confidence_before)

    def test_fresh_evidence_is_not_penalized(self) -> None:
        clock = _FakeClock(START)
        tracker = WorkContextTracker(clock=clock)
        tracker.record_evidence(EvidenceKind.OBSERVATION, "chrome_domain", "github.com")
        tracker.record_evidence(EvidenceKind.OBSERVATION, "chrome_domain", "github.com")
        confidence_before = tracker.context.confidence
        clock.advance(1.0)
        tracker.apply_staleness()
        self.assertEqual(tracker.context.confidence, confidence_before)


class WorkContextTrackerRedactionTest(unittest.TestCase):
    def test_secret_like_detail_is_redacted_before_storage(self) -> None:
        clock = _FakeClock(START)
        tracker = WorkContextTracker(clock=clock)
        evidence = tracker.record_evidence(
            EvidenceKind.OBSERVATION,
            "window_title",
            "login token=SUPERSECRET123 for session",
        )
        self.assertNotIn("SUPERSECRET123", evidence.detail)

    def test_full_url_is_never_persisted(self) -> None:
        self.assertNotIn("://", redact_sensitive("visit https://example.com/x?y=1"))

    def test_query_string_secret_is_redacted(self) -> None:
        redacted = redact_sensitive("https://example.com/reset?token=abc123XYZ")
        self.assertNotIn("abc123XYZ", redacted)


class WorkContextTrackerDistinguishesObservationFromInferenceTest(unittest.TestCase):
    def test_kind_is_preserved_and_distinct(self) -> None:
        clock = _FakeClock(START)
        tracker = WorkContextTracker(clock=clock)
        tracker.record_evidence(EvidenceKind.OBSERVATION, "window_title", "same detail")
        tracker.record_evidence(EvidenceKind.INFERENCE, "window_title", "same detail")
        self.assertEqual(len(tracker.context.evidence), 2)
        kinds = {item.kind for item in tracker.context.evidence}
        self.assertEqual(kinds, {EvidenceKind.OBSERVATION, EvidenceKind.INFERENCE})


UNSAFE_URL = "https://example.com/reset?token=SUPERSECRET123"
UNSAFE_ASSIGNMENT = "password=hunter2"


class EvidenceDirectConstructionPrivacyTest(unittest.TestCase):
    """Repair: privacy enforcement must apply at the domain boundary, not
    only inside WorkContextTracker call sites."""

    def test_unsafe_detail_raises_on_direct_construction(self) -> None:
        with self.assertRaises(ValueError):
            Evidence(
                kind=EvidenceKind.OBSERVATION,
                source="window_title",
                detail=UNSAFE_URL,
                observed_at=START,
            )

    def test_unsafe_source_raises_on_direct_construction(self) -> None:
        with self.assertRaises(ValueError):
            Evidence(
                kind=EvidenceKind.OBSERVATION,
                source=UNSAFE_ASSIGNMENT,
                detail="benign detail",
                observed_at=START,
            )

    def test_sanitized_detail_constructs_successfully(self) -> None:
        evidence = Evidence(
            kind=EvidenceKind.OBSERVATION,
            source="window_title",
            detail=redact_sensitive(UNSAFE_URL),
            observed_at=START,
        )
        self.assertNotIn("SUPERSECRET123", evidence.detail)
        self.assertNotIn("https://", evidence.detail)


class WorkContextDirectConstructionPrivacyTest(unittest.TestCase):
    """Every WorkContext field category must reject unsanitized unsafe text
    on direct construction: project, current_task, stage, and every list
    field."""

    def test_unsafe_project_raises(self) -> None:
        with self.assertRaises(ValueError):
            WorkContext(project=UNSAFE_URL, updated_at=START)

    def test_unsafe_current_task_raises(self) -> None:
        with self.assertRaises(ValueError):
            WorkContext(current_task=UNSAFE_ASSIGNMENT, updated_at=START)

    def test_unsafe_stage_raises(self) -> None:
        with self.assertRaises(ValueError):
            WorkContext(stage=UNSAFE_URL, updated_at=START)

    def test_unsafe_recent_action_raises(self) -> None:
        with self.assertRaises(ValueError):
            WorkContext(recent_actions=(UNSAFE_URL,), updated_at=START)

    def test_unsafe_recent_file_raises(self) -> None:
        with self.assertRaises(ValueError):
            WorkContext(recent_files=(UNSAFE_ASSIGNMENT,), updated_at=START)

    def test_unsafe_unfinished_work_raises(self) -> None:
        with self.assertRaises(ValueError):
            WorkContext(unfinished_work=(UNSAFE_URL,), updated_at=START)

    def test_unsafe_possible_next_step_raises(self) -> None:
        with self.assertRaises(ValueError):
            WorkContext(possible_next_steps=(UNSAFE_ASSIGNMENT,), updated_at=START)

    def test_sanitized_values_construct_successfully(self) -> None:
        context = WorkContext(
            project=redact_sensitive(UNSAFE_URL),
            current_task=redact_sensitive(UNSAFE_ASSIGNMENT),
            updated_at=START,
        )
        self.assertNotIn("https://", context.project)
        self.assertNotIn("hunter2", context.current_task)


class WorkContextTrackerSanitizesEveryEntryPointTest(unittest.TestCase):
    """The tracker never raises on unsafe input; it silently normalizes
    (redacts) it before storage — the opposite of direct construction."""

    def test_append_recent_action_sanitizes(self) -> None:
        tracker = WorkContextTracker(clock=_FakeClock(START))
        tracker.append_recent_action(UNSAFE_URL)
        self.assertNotIn("https://", tracker.context.recent_actions[0])

    def test_append_recent_file_sanitizes(self) -> None:
        tracker = WorkContextTracker(clock=_FakeClock(START))
        tracker.append_recent_file(UNSAFE_ASSIGNMENT)
        self.assertNotIn("hunter2", tracker.context.recent_files[0])

    def test_append_unfinished_work_sanitizes(self) -> None:
        tracker = WorkContextTracker(clock=_FakeClock(START))
        tracker.append_unfinished_work(UNSAFE_URL)
        self.assertNotIn("https://", tracker.context.unfinished_work[0])

    def test_append_possible_next_step_sanitizes(self) -> None:
        tracker = WorkContextTracker(clock=_FakeClock(START))
        tracker.append_possible_next_step(UNSAFE_ASSIGNMENT)
        self.assertNotIn("hunter2", tracker.context.possible_next_steps[0])

    def test_set_project_and_task_sanitizes(self) -> None:
        tracker = WorkContextTracker(clock=_FakeClock(START))
        tracker.record_evidence(EvidenceKind.OBSERVATION, "chrome_domain", "github.com")
        tracker.record_evidence(EvidenceKind.OBSERVATION, "chrome_domain", "github.com")
        tracker.set_project_and_task(
            project=UNSAFE_URL,
            current_task=UNSAFE_ASSIGNMENT,
            evidence_source="chrome_domain",
            evidence_detail="github.com",
        )
        self.assertNotIn("https://", tracker.context.project)
        self.assertNotIn("hunter2", tracker.context.current_task)

    def test_record_evidence_still_sanitizes_detail(self) -> None:
        tracker = WorkContextTracker(clock=_FakeClock(START))
        evidence = tracker.record_evidence(
            EvidenceKind.OBSERVATION, "window_title", UNSAFE_URL
        )
        self.assertNotIn("https://", evidence.detail)


if __name__ == "__main__":
    unittest.main()
