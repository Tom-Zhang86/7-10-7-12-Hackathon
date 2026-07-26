"""Structured work context and its evidence tracker (Master Spec section 5).

``WorkContext`` preserves the Master Spec's JSON contract exactly.
``WorkContextTracker`` is the deterministic, bounded component that mutates
it: it deduplicates evidence, requires corroboration before raising
confidence or setting ``project``/``current_task``, lowers confidence on
conflicts or staleness, and redacts likely secrets before anything is
persisted.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Callable
import re

from utils.time_utils import utc_now


class EvidenceKind(str, Enum):
    """Distinguishes a direct observation from a derived inference."""

    OBSERVATION = "observation"
    INFERENCE = "inference"


# Patterns for values that must never be persisted as evidence: obvious
# token/password/credential assignments, common secret-key prefixes, and
# secret-bearing query-string parameters. This list is intentionally small
# and documented rather than exhaustive — it is a redaction safety net, not
# a claim of complete secret detection.
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|passwd|bearer)\s*[:=]\s*\S+"
)
_SECRET_PREFIX_PATTERN = re.compile(
    r"(?i)\b(?:sk|pk|ghp|gho|xox[abp])-[A-Za-z0-9_-]{8,}\b"
)
_SECRET_QUERY_PARAM_PATTERN = re.compile(
    r"(?i)[?&][A-Za-z0-9_]*(token|key|secret|password|auth)[A-Za-z0-9_]*=[^&\s]+"
)
# Master Spec section 5/6: never persist a full browser URL.
_URL_PATTERN = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://\S+")


def redact_sensitive(text: str) -> str:
    """Strip likely secrets and full URLs before any evidence is persisted.

    Applied to every evidence ``detail`` before it is stored, so a
    credential or full URL that happens to appear in a window title, OCR
    result, or Chrome-adjacent metadata never reaches disk.
    """

    if not text:
        return ""
    redacted = _SECRET_QUERY_PARAM_PATTERN.sub("[REDACTED_PARAM]", text)
    redacted = _SECRET_ASSIGNMENT_PATTERN.sub("[REDACTED]", redacted)
    redacted = _SECRET_PREFIX_PATTERN.sub("[REDACTED]", redacted)
    redacted = _URL_PATTERN.sub("[REDACTED_URL]", redacted)
    return redacted.strip()


def _contains_unsafe_content(value: str) -> bool:
    """True if ``redact_sensitive`` would change this value for a reason
    *other than* trimming surrounding whitespace.

    Used as the domain-boundary privacy check: a value whose content (not
    merely its leading/trailing whitespace) would be altered by redaction
    is, by definition, carrying a full URL or credential-like content it
    should never have reached this object with. Comparing against
    ``value.strip()`` rather than ``value`` avoids flagging ordinary,
    harmless leading/trailing whitespace as "unsafe."
    """

    return redact_sensitive(value) != value.strip()


def _reject_unsafe_text(value: str, field_name: str) -> None:
    """Fail clearly on direct construction with unsanitized unsafe text.

    Phase 1 repair (2026-07-25 review): sanitization previously only
    happened at selected call sites (``WorkContextTracker.record_evidence``).
    Every domain object that stores free-text now enforces this itself, so
    there is no path — direct construction included — that can persist a
    full URL, a URL query parameter, or a token/password/API-key/bearer-like
    string. The chosen behavior is: callers that go through
    ``WorkContextTracker`` never see this error, because the tracker
    silently normalizes (redacts) input before constructing anything here;
    direct construction with raw, unsanitized unsafe text fails loudly
    instead of silently storing a partially-redacted surprise.
    """

    if _contains_unsafe_content(value):
        raise ValueError(
            f"{field_name} contains a full URL, a URL query parameter, or a "
            "token/password/API-key/bearer-like value; call "
            "redact_sensitive() on it before constructing this object"
        )


@dataclass(frozen=True)
class Evidence:
    """One piece of evidence backing a WorkContext (Master Spec section 5)."""

    kind: EvidenceKind
    source: str
    detail: str
    observed_at: datetime
    corroboration_count: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EvidenceKind):
            raise TypeError("kind must be an EvidenceKind")
        if not self.source or not self.source.strip():
            raise ValueError("source must be a non-empty string")
        if not self.detail or not self.detail.strip():
            raise ValueError("detail must be a non-empty string")
        _reject_unsafe_text(self.source, "source")
        _reject_unsafe_text(self.detail, "detail")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        if not isinstance(self.corroboration_count, int) or self.corroboration_count < 1:
            raise ValueError("corroboration_count must be an integer >= 1")

    def as_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "source": self.source,
            "detail": self.detail,
            "observed_at": self.observed_at.isoformat(),
            "corroboration_count": self.corroboration_count,
        }


def _string_tuple(name: str, value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"{name} must be a tuple of strings")
    return value


@dataclass(frozen=True)
class WorkContext:
    """Preserves the Master Spec section 5 JSON contract exactly."""

    project: str = ""
    current_task: str = ""
    stage: str = ""
    recent_actions: tuple[str, ...] = ()
    recent_files: tuple[str, ...] = ()
    unfinished_work: tuple[str, ...] = ()
    possible_next_steps: tuple[str, ...] = ()
    confidence: float = 0.0
    evidence: tuple[Evidence, ...] = ()
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for name in ("project", "current_task", "stage"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise TypeError(f"{name} must be a string")
            _reject_unsafe_text(value, name)
        for name in (
            "recent_actions",
            "recent_files",
            "unfinished_work",
            "possible_next_steps",
        ):
            values = _string_tuple(name, getattr(self, name))
            for item in values:
                _reject_unsafe_text(item, name)
        if (
            not isinstance(self.confidence, (int, float))
            or isinstance(self.confidence, bool)
            or not (0.0 <= float(self.confidence) <= 1.0)
        ):
            raise ValueError("confidence must be a number between 0.0 and 1.0")
        if not isinstance(self.evidence, tuple) or not all(
            isinstance(item, Evidence) for item in self.evidence
        ):
            raise TypeError("evidence must be a tuple of Evidence")
        if self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")

    def as_dict(self) -> dict:
        """Deterministic serialization matching the Master Spec contract."""

        return {
            "project": self.project,
            "current_task": self.current_task,
            "stage": self.stage,
            "recent_actions": list(self.recent_actions),
            "recent_files": list(self.recent_files),
            "unfinished_work": list(self.unfinished_work),
            "possible_next_steps": list(self.possible_next_steps),
            "confidence": round(float(self.confidence), 6),
            "evidence": [item.as_dict() for item in self.evidence],
            "updated_at": self.updated_at.isoformat(),
        }


class WorkContextTracker:
    """Deterministic, bounded evidence tracker driving one WorkContext.

    Confidence rises only after evidence is corroborated (never from a
    single observation) and falls on conflicts or staleness. The clock is
    injectable so staleness is testable without real sleeps.
    """

    MAX_LIST_ITEMS = 10
    MAX_EVIDENCE_ITEMS = 20
    CORROBORATION_REQUIRED = 2
    CONFIDENCE_STEP = 0.2
    ADDITIONAL_CORROBORATION_STEP = 0.05
    CONFLICT_PENALTY = 0.3
    STALE_PENALTY = 0.2
    STALE_AFTER_SECONDS = 900.0

    def __init__(
        self,
        clock: Callable[[], datetime] = utc_now,
        context: WorkContext | None = None,
    ) -> None:
        self._clock = clock
        self._context = context if context is not None else WorkContext(updated_at=clock())

    @property
    def context(self) -> WorkContext:
        return self._context

    def _find(self, kind: EvidenceKind, source: str, detail: str) -> Evidence | None:
        for item in self._context.evidence:
            if item.kind is kind and item.source == source and item.detail == detail:
                return item
        return None

    def corroboration_count(self, kind: EvidenceKind, source: str, detail: str) -> int:
        clean_detail = redact_sensitive(detail)
        existing = self._find(kind, source, clean_detail)
        return existing.corroboration_count if existing else 0

    def record_evidence(self, kind: EvidenceKind, source: str, detail: str) -> Evidence:
        """Add new evidence, or corroborate an existing (kind, source, detail).

        Deduplicates by (kind, source, detail): a repeat increments
        ``corroboration_count`` in place rather than adding a new entry.
        """

        clean_detail = redact_sensitive(detail)
        if not clean_detail:
            raise ValueError("detail must not be empty after redaction")
        now = self._clock()
        existing = self._find(kind, source, clean_detail)

        if existing is not None:
            updated = replace(
                existing,
                corroboration_count=existing.corroboration_count + 1,
                observed_at=now,
            )
            new_evidence = tuple(
                updated if item is existing else item for item in self._context.evidence
            )
            confidence = self._context.confidence
            # Confidence only rises once corroboration crosses the required
            # threshold (never from a single, uncorroborated observation),
            # per Master Spec section 5.
            if updated.corroboration_count == self.CORROBORATION_REQUIRED:
                confidence = min(1.0, confidence + self.CONFIDENCE_STEP)
            elif updated.corroboration_count > self.CORROBORATION_REQUIRED:
                confidence = min(1.0, confidence + self.ADDITIONAL_CORROBORATION_STEP)
            self._context = replace(
                self._context, evidence=new_evidence, confidence=confidence, updated_at=now
            )
            return updated

        new_item = Evidence(kind=kind, source=source, detail=clean_detail, observed_at=now)
        bounded = (self._context.evidence + (new_item,))[-self.MAX_EVIDENCE_ITEMS :]
        self._context = replace(self._context, evidence=bounded, updated_at=now)
        return new_item

    def set_project_and_task(
        self,
        *,
        project: str,
        current_task: str,
        evidence_source: str,
        evidence_detail: str,
    ) -> bool:
        """Set project/current_task only if backing evidence is corroborated.

        A single observation must never establish a project or task (Master
        Spec section 5: "do not infer a project from one random keyword").
        Returns whether the update was applied.
        """

        count = self.corroboration_count(
            EvidenceKind.OBSERVATION, evidence_source, evidence_detail
        )
        if count < self.CORROBORATION_REQUIRED:
            return False
        self._context = replace(
            self._context,
            project=redact_sensitive(project),
            current_task=redact_sensitive(current_task),
            updated_at=self._clock(),
        )
        return True

    def _bounded_append(self, values: tuple[str, ...], value: str) -> tuple[str, ...]:
        if value in values:
            return values
        return (values + (value,))[-self.MAX_LIST_ITEMS :]

    def _sanitized(self, value: str, field_name: str) -> str:
        """Sanitize before storage — the tracker normalizes silently rather
        than raising, unlike direct construction of ``WorkContext``/
        ``Evidence`` (Phase 1 repair: privacy sanitization must apply at
        every entry point, not merely selected call sites)."""

        cleaned = redact_sensitive(value)
        if not cleaned:
            raise ValueError(f"{field_name} must not be empty after redaction")
        return cleaned

    def append_recent_action(self, action: str) -> None:
        self._context = replace(
            self._context,
            recent_actions=self._bounded_append(
                self._context.recent_actions,
                self._sanitized(action, "action"),
            ),
            updated_at=self._clock(),
        )

    def append_recent_file(self, filename: str) -> None:
        self._context = replace(
            self._context,
            recent_files=self._bounded_append(
                self._context.recent_files,
                self._sanitized(filename, "filename"),
            ),
            updated_at=self._clock(),
        )

    def append_unfinished_work(self, item: str) -> None:
        self._context = replace(
            self._context,
            unfinished_work=self._bounded_append(
                self._context.unfinished_work,
                self._sanitized(item, "item"),
            ),
            updated_at=self._clock(),
        )

    def append_possible_next_step(self, item: str) -> None:
        self._context = replace(
            self._context,
            possible_next_steps=self._bounded_append(
                self._context.possible_next_steps,
                self._sanitized(item, "item"),
            ),
            updated_at=self._clock(),
        )

    def register_conflict(self) -> None:
        """Lower confidence when new evidence conflicts with prior evidence."""

        self._context = replace(
            self._context,
            confidence=max(0.0, self._context.confidence - self.CONFLICT_PENALTY),
            updated_at=self._clock(),
        )

    def apply_staleness(self) -> None:
        """Lower confidence once the context has gone stale.

        Compares the injected clock's current time against
        ``context.updated_at`` so staleness is deterministic and testable
        without real sleeps.
        """

        now = self._clock()
        age_seconds = (now - self._context.updated_at).total_seconds()
        if age_seconds >= self.STALE_AFTER_SECONDS:
            self._context = replace(
                self._context,
                confidence=max(0.0, self._context.confidence - self.STALE_PENALTY),
            )
