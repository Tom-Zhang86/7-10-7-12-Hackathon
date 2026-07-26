import unittest
from datetime import datetime, timezone

from application.owner_handoff.classification.context_analyzer import ContextAnalyzer
from application.owner_handoff.domain.activity import (
    ActivityClassification,
    ActivitySample,
)
from application.owner_handoff.domain.work_context import (
    Evidence,
    EvidenceKind,
    WorkContext,
)

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


def _sample(**overrides) -> ActivitySample:
    defaults = dict(observed_at=NOW, duration_seconds=30.0)
    defaults.update(overrides)
    return ActivitySample(**defaults)


def _context(*evidence: Evidence) -> WorkContext:
    return WorkContext(evidence=tuple(evidence), updated_at=NOW)


def _prior(source: str, detail: str, count: int = 1) -> Evidence:
    return Evidence(
        kind=EvidenceKind.OBSERVATION,
        source=source,
        detail=detail,
        observed_at=NOW,
        corroboration_count=count,
    )


class ContextAnalyzerRepeatedEvidenceTest(unittest.TestCase):
    def test_same_signal_repeated_resolves_working(self) -> None:
        prior = _prior("chrome_domain", "github.com")
        sample = _sample(chrome_domain="github.com")
        result = ContextAnalyzer().analyze(sample, _context(prior))
        self.assertEqual(result.classification, ActivityClassification.WORKING)


class ContextAnalyzerDistinctSourceTest(unittest.TestCase):
    def test_two_distinct_source_types_corroborate(self) -> None:
        sample = _sample(
            window_title="main.py - editing",
            visible_filename="main.py",
        )
        result = ContextAnalyzer().analyze(sample, _context())
        self.assertEqual(result.classification, ActivityClassification.WORKING)


class ContextAnalyzerSingleKeywordTest(unittest.TestCase):
    def test_single_uncorroborated_keyword_does_not_resolve(self) -> None:
        sample = _sample(chrome_domain="github.com")
        result = ContextAnalyzer().analyze(sample, _context())
        self.assertEqual(result.classification, ActivityClassification.AMBIGUOUS)


class ContextAnalyzerDualUseDomainRegressionTest(unittest.TestCase):
    """Phase 1 repair: repeated dual-use domains must never become NOT_WORKING."""

    def test_repeated_youtube_domain_alone_stays_ambiguous(self) -> None:
        prior = _prior("chrome_domain", "youtube.com", count=5)
        sample = _sample(chrome_domain="youtube.com")
        result = ContextAnalyzer().analyze(sample, _context(prior))
        self.assertEqual(result.classification, ActivityClassification.AMBIGUOUS)

    def test_youtube_corroborated_across_two_source_types_still_stays_ambiguous(
        self,
    ) -> None:
        sample = _sample(
            chrome_domain="youtube.com",
            window_title="youtube.com - some video",
        )
        result = ContextAnalyzer().analyze(sample, _context())
        self.assertEqual(result.classification, ActivityClassification.AMBIGUOUS)


class ContextAnalyzerCommunicationRegressionTest(unittest.TestCase):
    """Phase 1 repair: repeated communication surfaces must never become
    NOT_WORKING merely because the app/domain appeared repeatedly."""

    def test_repeated_slack_domain_alone_stays_ambiguous(self) -> None:
        prior = _prior("chrome_domain", "slack.com", count=5)
        sample = _sample(chrome_domain="slack.com")
        result = ContextAnalyzer().analyze(sample, _context(prior))
        self.assertEqual(result.classification, ActivityClassification.AMBIGUOUS)

    def test_repeated_mail_domain_alone_stays_ambiguous(self) -> None:
        prior = _prior("chrome_domain", "mail.google.com", count=5)
        sample = _sample(chrome_domain="mail.google.com")
        result = ContextAnalyzer().analyze(sample, _context(prior))
        self.assertEqual(result.classification, ActivityClassification.AMBIGUOUS)


class ContextAnalyzerContentSpecificEntertainmentTest(unittest.TestCase):
    """NOT_WORKING may be resolved only by content-specific, corroborated
    entertainment evidence — never by a bare, repeated domain name."""

    def test_corroborated_content_specific_marker_resolves_not_working(self) -> None:
        prior = _prior("window_title", "season 3 episode 2 gameplay highlights")
        sample = _sample(window_title="season 3 episode 2 gameplay highlights")
        result = ContextAnalyzer().analyze(sample, _context(prior))
        self.assertEqual(result.classification, ActivityClassification.NOT_WORKING)

    def test_single_uncorroborated_content_marker_does_not_resolve(self) -> None:
        sample = _sample(window_title="official trailer for the new season")
        result = ContextAnalyzer().analyze(sample, _context())
        self.assertEqual(result.classification, ActivityClassification.AMBIGUOUS)


class ContextAnalyzerConflictTest(unittest.TestCase):
    def test_conflicting_strong_task_and_entertainment_content_stays_ambiguous(
        self,
    ) -> None:
        prior_task = _prior("chrome_domain", "github.com")
        prior_entertainment = _prior("window_title", "watch party gameplay tonight")
        sample = _sample(
            chrome_domain="github.com",
            window_title="watch party gameplay tonight",
        )
        result = ContextAnalyzer().analyze(
            sample, _context(prior_task, prior_entertainment)
        )
        self.assertEqual(result.classification, ActivityClassification.AMBIGUOUS)

    def test_weak_uncorroborated_entertainment_mention_does_not_overwrite_strong_task_evidence(
        self,
    ) -> None:
        prior_task = _prior("chrome_domain", "github.com", count=2)
        sample = _sample(
            chrome_domain="github.com",
            window_title="one quick trailer",
        )
        result = ContextAnalyzer().analyze(sample, _context(prior_task))
        self.assertEqual(result.classification, ActivityClassification.WORKING)

    def test_repeated_communication_domain_never_overwrites_strong_task_evidence(
        self,
    ) -> None:
        prior_task = _prior("chrome_domain", "github.com", count=2)
        prior_comm = _prior("window_title", "slack.com messages", count=10)
        sample = _sample(
            chrome_domain="github.com",
            window_title="slack.com messages",
        )
        result = ContextAnalyzer().analyze(sample, _context(prior_task, prior_comm))
        self.assertEqual(result.classification, ActivityClassification.WORKING)


class ContextAnalyzerSecretRedactionTest(unittest.TestCase):
    def test_full_url_and_query_secret_are_not_persisted(self) -> None:
        sample = _sample(
            window_title="Reset link https://example.com/reset?token=SUPERSECRET123",
        )
        result = ContextAnalyzer().analyze(sample, _context())
        details = [item.detail for item in result.candidate_evidence]
        joined = " ".join(details)
        self.assertNotIn("SUPERSECRET123", joined)
        self.assertNotIn("https://", joined)


if __name__ == "__main__":
    unittest.main()
