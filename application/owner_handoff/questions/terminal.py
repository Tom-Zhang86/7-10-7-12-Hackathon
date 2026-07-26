"""Terminal rendering of a handoff question (Master Spec section 9).

The full question — context summary, question text, every option, and
expiration — is displayed in Terminal. The wearable receives a disjoint,
minimal payload: only the question_id and the available letters, never the
question text, context summary, filenames, or any workspace content. This
module renders text only; it never reads a task selection from the
keyboard (there is no ``input()`` call anywhere here).
"""
from __future__ import annotations

from typing import TextIO

from application.owner_handoff.domain.question import HandoffQuestion

_OPTION_ORDER = ("A", "B", "C", "D")


def render_question(question: HandoffQuestion, stream: TextIO) -> None:
    """Write the complete question to ``stream`` (injectable for tests —
    pass ``io.StringIO()`` to capture output without touching real stdout)."""

    stream.write("=" * 60 + "\n")
    stream.write("AI Desk handoff question\n")
    stream.write("=" * 60 + "\n")
    stream.write(f"Context: {question.context_summary}\n")
    stream.write("\n")
    stream.write(f"{question.question}\n")
    for letter in _OPTION_ORDER:
        if letter in question.options:
            stream.write(f"  {letter}) {question.options[letter]}\n")
    stream.write("\n")
    stream.write(f"Question ID: {question.question_id}\n")
    stream.write(f"Expires at:  {question.expires_at.isoformat()}\n")


def wearable_payload(question: HandoffQuestion) -> tuple[str, tuple[str, ...]]:
    """Return exactly (question_id, available letters) — nothing else.

    This is the only representation of a question ever handed to a
    wearable transport; callers must not pass ``question`` itself (or any
    of its other fields) to ``WearableTransport.send_question``.
    """

    letters = tuple(letter for letter in _OPTION_ORDER if letter in question.options)
    return question.question_id, letters
