"""Tier 2 context analysis (Master Spec section 4, 6).

Runs only when Tier 1 (``ActivityClassifier``) returns AMBIGUOUS. Uses
sanitized window title, Chrome domain (never a full URL — enforced by
``ActivitySample`` itself), visible filename, and the current
``WorkContext``'s existing evidence. ``ContextAnalyzer`` never marks
anything as "completed": it only ever returns a classification plus
candidate observation evidence, so a caller can never derive a completed-work
claim from window/app activity through this component.

Phase 1 repair (2026-07-25 review): the original version of this analyzer
let a repeated communication or dual-use domain (e.g. ``slack.com``,
``youtube.com``) resolve NOT_WORKING purely because the *domain name*
recurred. That was wrong: an app/domain appearing repeatedly is not content
evidence that the owner is off-task — communication tools are routinely used
for work, and video platforms are routinely used for tutorials/documentation.
NOT_WORKING may now only be resolved by *content-specific* entertainment
markers (see ``_ENTERTAINMENT_CONTENT_MARKERS`` below) — substrings that
describe actual entertainment content, not just an app/domain name — and
even those still require the same corroboration Tier 2 requires everywhere
else. Communication domains never contribute to a NOT_WORKING result at all,
regardless of repetition.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from application.owner_handoff.domain.activity import (
    ActivityClassification,
    ActivitySample,
)
from application.owner_handoff.domain.work_context import (
    Evidence,
    EvidenceKind,
    WorkContext,
    redact_sensitive,
)

# A signal must either recur this many times for the same (source, detail),
# or be corroborated by this many distinct source types within one sample,
# before Tier 2 will resolve WORKING or NOT_WORKING. A single matching
# keyword must never resolve anything (Master Spec section 6).
RESOLUTION_CORROBORATION_THRESHOLD = 2
RESOLUTION_SOURCE_TYPE_THRESHOLD = 2

SOURCE_WINDOW_TITLE = "window_title"
SOURCE_CHROME_DOMAIN = "chrome_domain"
SOURCE_VISIBLE_FILENAME = "visible_filename"

# Small, bounded hint lists (not exhaustive "final truth") reused from the
# same spirit as Tier 1's app hints, applied here to domain/filename/title
# substrings instead of app names.
_TASK_DOMAIN_HINTS = frozenset(
    {
        "github.com",
        "stackoverflow.com",
        "docs.python.org",
        "developer.mozilla.org",
    }
)
# File-extension hints are matched with a word-boundary-aware regex, not a
# naive substring check: a naive "in" check on ".go" would false-positive
# inside "google.com" (contains the literal substring ".go"). The pattern
# below requires the extension not be immediately followed by another
# letter/digit, so "main.go" matches but "google.com" does not.
_TASK_EXTENSION_PATTERN = re.compile(
    r"\.(py|ts|tsx|js|md|json|java|go|rs)(?![a-zA-Z0-9])"
)

# Communication domains. These may still be recorded as candidate evidence
# (useful context for a human reviewing WorkContext), but per the Phase 1
# repair they can NEVER, by themselves, resolve NOT_WORKING — a
# communication tool appearing repeatedly proves nothing about whether the
# communication itself is work-related.
_COMMUNICATION_DOMAIN_HINTS = frozenset(
    {
        "slack.com",
        "mail.google.com",
        "outlook.live.com",
        "teams.microsoft.com",
        "zoom.us",
        "wechat.com",
    }
)

# Dual-use / entertainment-leaning domains. Like communication domains,
# these can be recorded as evidence but the bare domain name — no matter how
# often it recurs — can never resolve NOT_WORKING on its own (Phase 1
# repair). Only a genuinely content-specific marker (see below) can.
_DUAL_USE_ENTERTAINMENT_DOMAIN_HINTS = frozenset(
    {
        "youtube.com",
        "twitch.tv",
        "tiktok.com",
        "instagram.com",
        "netflix.com",
    }
)

# Content-specific entertainment markers: substrings that describe actual
# entertainment CONTENT (e.g. a video's title or genre), as opposed to an
# app/domain name. Only these — corroborated the same way task evidence is —
# may resolve NOT_WORKING.
_ENTERTAINMENT_CONTENT_MARKERS = frozenset(
    {
        "gameplay",
        "trailer",
        "episode",
        "season",
        "official music video",
        "highlights",
        "movie night",
        "watch party",
        "livestream vod",
    }
)

_TASK = "task"
_COMMUNICATION = "communication"
_DUAL_USE_DOMAIN = "dual_use_domain"
_ENTERTAINMENT_CONTENT = "entertainment_content"

# Categories whose corroborated resolution can actually decide the final
# classification. _COMMUNICATION and _DUAL_USE_DOMAIN are deliberately
# excluded: they are recorded as evidence but never drive a result.
_RESOLVING_CATEGORIES = (_TASK, _ENTERTAINMENT_CONTENT)


def _hint_category(value: str) -> str | None:
    lowered = value.lower()
    if any(hint in lowered for hint in _ENTERTAINMENT_CONTENT_MARKERS):
        return _ENTERTAINMENT_CONTENT
    if any(hint in lowered for hint in _TASK_DOMAIN_HINTS) or _TASK_EXTENSION_PATTERN.search(
        lowered
    ):
        return _TASK
    if any(hint in lowered for hint in _COMMUNICATION_DOMAIN_HINTS):
        return _COMMUNICATION
    if any(hint in lowered for hint in _DUAL_USE_ENTERTAINMENT_DOMAIN_HINTS):
        return _DUAL_USE_DOMAIN
    return None


@dataclass(frozen=True)
class ContextAnalysisResult:
    classification: ActivityClassification
    candidate_evidence: tuple[Evidence, ...]


class ContextAnalyzer:
    """Tier 2 resolution using sanitized window/domain/filename metadata."""

    def analyze(
        self, sample: ActivitySample, work_context: WorkContext
    ) -> ContextAnalysisResult:
        signals = self._extract_signals(sample)
        candidate_evidence = tuple(
            Evidence(
                kind=EvidenceKind.OBSERVATION,
                source=source,
                detail=detail,
                observed_at=sample.observed_at,
            )
            for source, detail in signals
        )

        task_repeat, task_sources = self._strength(signals, work_context, _TASK)
        entertainment_repeat, entertainment_sources = self._strength(
            signals, work_context, _ENTERTAINMENT_CONTENT
        )

        task_resolved = (
            task_repeat >= RESOLUTION_CORROBORATION_THRESHOLD
            or task_sources >= RESOLUTION_SOURCE_TYPE_THRESHOLD
        )
        entertainment_resolved = (
            entertainment_repeat >= RESOLUTION_CORROBORATION_THRESHOLD
            or entertainment_sources >= RESOLUTION_SOURCE_TYPE_THRESHOLD
        )

        if task_resolved and not entertainment_resolved:
            # A resolved-but-weaker entertainment mention must not overwrite
            # stronger, repeated task evidence.
            classification = ActivityClassification.WORKING
        elif entertainment_resolved and not task_resolved:
            classification = ActivityClassification.NOT_WORKING
        else:
            # Neither resolved, or both resolved (a genuine conflict): stay
            # AMBIGUOUS (Master Spec section 6: "conflicting evidence
            # remains AMBIGUOUS"). Communication/dual-use-domain evidence
            # alone always lands here too, by construction — see
            # _RESOLVING_CATEGORIES.
            classification = ActivityClassification.AMBIGUOUS

        return ContextAnalysisResult(
            classification=classification, candidate_evidence=candidate_evidence
        )

    def _extract_signals(self, sample: ActivitySample) -> tuple[tuple[str, str], ...]:
        raw = (
            (SOURCE_WINDOW_TITLE, sample.window_title),
            (SOURCE_CHROME_DOMAIN, sample.chrome_domain),
            (SOURCE_VISIBLE_FILENAME, sample.visible_filename),
        )
        signals: list[tuple[str, str]] = []
        for source, value in raw:
            if not value:
                continue
            cleaned = redact_sensitive(value)
            if cleaned:
                signals.append((source, cleaned))
        return tuple(signals)

    def _strength(
        self,
        signals: tuple[tuple[str, str], ...],
        work_context: WorkContext,
        category: str,
    ) -> tuple[int, int]:
        """Return (max same-detail corroboration, distinct source-type count).

        Both counts are computed only from signals present in *this* sample
        (optionally corroborated by matching prior evidence) — stale,
        no-longer-relevant history alone can never resolve a brand new,
        unrelated sample. Only categories in ``_RESOLVING_CATEGORIES`` are
        ever passed in by ``analyze``; this method itself has no opinion on
        which categories may decide a result.
        """

        max_repeat = 0
        source_types: set[str] = set()
        for source, detail in signals:
            if _hint_category(detail) != category:
                continue
            source_types.add(source)
            repeat = 1
            for item in work_context.evidence:
                if (
                    item.kind is EvidenceKind.OBSERVATION
                    and item.source == source
                    and item.detail == detail
                ):
                    repeat = item.corroboration_count + 1
                    break
            max_repeat = max(max_repeat, repeat)
        return max_repeat, len(source_types)
