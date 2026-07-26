"""Conservative task/skill routing (Master Spec section 9, 12).

Routing never changes the external ``HandoffQuestion`` JSON contract — it
only produces a companion ``HandoffPlan`` describing how each option would
be routed if selected. Classification deliberately requires *both* a clear
action verb *and* supporting context (a matching object/artifact keyword)
from the same skill category before committing to RESEARCH or CODING; a
single weak keyword, a missing verb, or conflicting hints from both
categories all fail closed to UNSUPPORTED — an unsupported/ambiguous route
must never execute anything (Master Spec section 12).
"""
from __future__ import annotations

import re

from application.owner_handoff.domain.execution import HandoffPlan, RoutedOption, SkillKind
from application.owner_handoff.domain.question import HandoffQuestion

# Bounded, documented hint lists -- not exhaustive "final truth" lists, the
# same spirit as the activity/context classifiers' hint tables.
_CODING_VERBS = frozenset(
    {
        "fix",
        "implement",
        "add",
        "write",
        "draft",
        "wire",
        "refactor",
        "update",
        "debug",
        "patch",
        "continue",
        "finish",
    }
)
_CODING_OBJECTS = frozenset(
    {
        "test",
        "tests",
        "pr",
        "bug",
        "function",
        "file",
        "code",
        "script",
        "module",
        "class",
        "feature",
        "renderer",
        "generator",
        "endpoint",
        "docstring",
        "readme",
        "documentation",
    }
)

_RESEARCH_VERBS = frozenset(
    {
        "research",
        "investigate",
        "explore",
        "compare",
        "study",
        "review",
        "look into",
        "read about",
        "find out",
        "survey",
    }
)
_RESEARCH_OBJECTS = frozenset(
    {
        "issue",
        "topic",
        "approach",
        "literature",
        "options",
        "alternatives",
        "documentation",
        "paper",
        "article",
        "question",
    }
)


def _compile_token_pattern(phrases: frozenset[str]) -> re.Pattern[str]:
    """Compile a token/phrase-boundary pattern for a hint list.

    Repair (Phase 3 gate): the previous implementation used plain substring
    matching (``keyword in lowered``), which matched "fix" inside "prefix"
    or "class" inside "classification" -- an accidental substring inside a
    longer, unrelated word must never count as a hint. Longer phrases are
    tried first so a multi-word phrase like "look into" is matched whole
    rather than via one of its shorter component words. ``(?<![\\w-])`` /
    ``(?![\\w-])`` require a non-word, non-hyphen character (or
    start/end of string) on both sides of the match -- true word/phrase
    boundaries, not merely substring containment.
    """

    ordered = sorted(phrases, key=len, reverse=True)
    alternation = "|".join(re.escape(phrase) for phrase in ordered)
    return re.compile(rf"(?<![\w-])(?:{alternation})(?![\w-])")


_CODING_VERB_PATTERN = _compile_token_pattern(_CODING_VERBS)
_CODING_OBJECT_PATTERN = _compile_token_pattern(_CODING_OBJECTS)
_RESEARCH_VERB_PATTERN = _compile_token_pattern(_RESEARCH_VERBS)
_RESEARCH_OBJECT_PATTERN = _compile_token_pattern(_RESEARCH_OBJECTS)


def classify_goal_text(text: str) -> SkillKind:
    """Conservative single-text classifier.

    Requires a verb AND a supporting object from the SAME category, each
    matched at a true token/phrase boundary (never an accidental substring
    match inside a longer, unrelated word). A match in only one category
    resolves to that skill; a match in BOTH categories (conflicting hints)
    or NEITHER (ambiguous/unsupported) fails closed to
    ``SkillKind.UNSUPPORTED`` — never guessed.
    """

    lowered = text.lower()
    is_coding = bool(_CODING_VERB_PATTERN.search(lowered)) and bool(
        _CODING_OBJECT_PATTERN.search(lowered)
    )
    is_research = bool(_RESEARCH_VERB_PATTERN.search(lowered)) and bool(
        _RESEARCH_OBJECT_PATTERN.search(lowered)
    )

    if is_coding and is_research:
        return SkillKind.UNSUPPORTED
    if is_coding:
        return SkillKind.CODING
    if is_research:
        return SkillKind.RESEARCH
    return SkillKind.UNSUPPORTED


def route_handoff_question(question: HandoffQuestion) -> HandoffPlan:
    """Build the companion routing plan for one question. D always routes
    to DO_NOTHING; every other offered option is classified independently."""

    routes: dict[str, RoutedOption] = {}
    for letter, text in question.options.items():
        if letter == "D":
            routes[letter] = RoutedOption(letter=letter, skill=SkillKind.DO_NOTHING, goal_text=text)
        else:
            routes[letter] = RoutedOption(
                letter=letter, skill=classify_goal_text(text), goal_text=text
            )
    return HandoffPlan(question_id=question.question_id, routes=routes)
