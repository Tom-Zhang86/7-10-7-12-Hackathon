import unittest
from datetime import datetime, timedelta, timezone

from application.owner_handoff.domain.execution import SkillKind
from application.owner_handoff.domain.question import HandoffQuestion
from application.owner_handoff.routing.skills import classify_goal_text, route_handoff_question

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


def _question(options: dict) -> HandoffQuestion:
    return HandoffQuestion(
        question_id="q-1",
        context_summary="AI Desk: draft PR",
        question="What should AI Desk do?",
        options=options,
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=60),
    )


class SkillRoutingResearchTest(unittest.TestCase):
    def test_research_verb_and_object_routes_to_research(self) -> None:
        self.assertEqual(
            classify_goal_text("Research the presence fusion approach"),
            SkillKind.RESEARCH,
        )

    def test_investigate_topic_routes_to_research(self) -> None:
        self.assertEqual(
            classify_goal_text("Investigate the alternatives for this topic"),
            SkillKind.RESEARCH,
        )


class SkillRoutingCodingTest(unittest.TestCase):
    def test_fix_test_routes_to_coding(self) -> None:
        self.assertEqual(
            classify_goal_text("Fix the failing test in the renderer"),
            SkillKind.CODING,
        )

    def test_draft_pr_routes_to_coding(self) -> None:
        self.assertEqual(classify_goal_text("Draft the PR for this feature"), SkillKind.CODING)


class SkillRoutingDoNothingTest(unittest.TestCase):
    def test_option_d_always_routes_to_do_nothing(self) -> None:
        question = _question(
            {"A": "Fix the failing test", "B": "Research the alternatives", "D": "Do nothing"}
        )
        plan = route_handoff_question(question)
        self.assertEqual(plan.route_for("D").skill, SkillKind.DO_NOTHING)


class SkillRoutingAmbiguousTest(unittest.TestCase):
    def test_no_verb_or_object_match_is_unsupported(self) -> None:
        self.assertEqual(classify_goal_text("Look at the thing"), SkillKind.UNSUPPORTED)

    def test_verb_without_supporting_object_is_unsupported(self) -> None:
        # "fix" alone, no coding-object keyword present.
        self.assertEqual(classify_goal_text("Fix it soon"), SkillKind.UNSUPPORTED)

    def test_object_without_verb_is_unsupported(self) -> None:
        self.assertEqual(classify_goal_text("The test suite"), SkillKind.UNSUPPORTED)


class SkillRoutingConflictingTest(unittest.TestCase):
    def test_both_coding_and_research_signals_is_unsupported(self) -> None:
        self.assertEqual(
            classify_goal_text("Fix the research approach for the test issue"),
            SkillKind.UNSUPPORTED,
        )


class SkillRoutingTokenBoundaryTest(unittest.TestCase):
    """Repair regressions: a hint keyword must match at a token/phrase
    boundary, never as an accidental substring inside a longer word."""

    def test_prefix_the_classification_report_is_unsupported(self) -> None:
        self.assertEqual(
            classify_goal_text("Prefix the classification report"),
            SkillKind.UNSUPPORTED,
        )

    def test_fix_substring_inside_longer_word_does_not_count_as_verb(self) -> None:
        # "prefix" contains "fix" as a substring; with a real coding object
        # ("test") present, the old substring-matching implementation
        # would have wrongly classified this as CODING.
        self.assertEqual(
            classify_goal_text("Prefix the failing test suite"),
            SkillKind.UNSUPPORTED,
        )

    def test_class_substring_inside_classification_does_not_count_as_object(self) -> None:
        # "classification" contains "class" as a substring; with a real
        # coding verb ("fix") present, this must still not resolve to CODING.
        self.assertEqual(
            classify_goal_text("Fix the classification report"),
            SkillKind.UNSUPPORTED,
        )

    def test_accidental_matches_inside_longer_words_never_match(self) -> None:
        for text in (
            "Prefix the classification report",
            "Refixture the endpointless module",
            "The suffixed readmelike document",
        ):
            with self.subTest(text=text):
                self.assertEqual(classify_goal_text(text), SkillKind.UNSUPPORTED)

    def test_verb_only_text_is_unsupported(self) -> None:
        self.assertEqual(classify_goal_text("implement"), SkillKind.UNSUPPORTED)

    def test_object_only_text_is_unsupported(self) -> None:
        self.assertEqual(classify_goal_text("bug"), SkillKind.UNSUPPORTED)

    def test_multi_word_research_phrase_matches_at_boundary(self) -> None:
        self.assertEqual(
            classify_goal_text("Look into the alternatives for this approach"),
            SkillKind.RESEARCH,
        )


class SkillRoutingUnsupportedTest(unittest.TestCase):
    def test_unsupported_option_never_produces_research_or_coding(self) -> None:
        question = _question(
            {
                "A": "Look at the thing",
                "B": "Fix the research approach for the test issue",
                "D": "Do nothing",
            }
        )
        plan = route_handoff_question(question)
        self.assertEqual(plan.route_for("A").skill, SkillKind.UNSUPPORTED)
        self.assertEqual(plan.route_for("B").skill, SkillKind.UNSUPPORTED)

    def test_plan_preserves_goal_text_for_audit(self) -> None:
        question = _question(
            {"A": "Fix the failing test", "B": "Research the alternatives", "D": "Do nothing"}
        )
        plan = route_handoff_question(question)
        self.assertEqual(plan.route_for("A").goal_text, "Fix the failing test")


if __name__ == "__main__":
    unittest.main()
