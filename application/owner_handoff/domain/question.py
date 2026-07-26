"""Handoff and permission question domain models (Master Spec section 9,
14, 15).

``HandoffQuestion`` preserves the Master Spec's JSON contract exactly, and
uses A/B/C/D options. ``PermissionQuestion`` is a distinct type for
CODEX_DATA/PACKAGE_INSTALL authorization prompts, and uses exactly YES/NO —
Phase 2 repair: permission questions must never be represented as a
``HandoffQuestion`` (different contract, different button family, different
required fields). Both are immutable — mapping fields are wrapped in
``MappingProxyType`` so a caller cannot mutate a supposedly-frozen question
after construction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Mapping

from application.owner_handoff.domain.work_context import redact_sensitive
from application.owner_handoff.state_machine import PermissionKind


def _contains_unsafe_content(value: str) -> bool:
    """True if ``redact_sensitive`` would change this value for a reason
    other than trimming whitespace — see ``domain.work_context`` for the
    equivalent, canonical check (kept in sync; not imported directly since
    it is a private helper there)."""

    return redact_sensitive(value) != value.strip()

# Valid shapes for `options`, per Master Spec section 9: D is always
# present ("Do nothing"); real directions are either absent (low-confidence
# / clarification-only case), two (A, B), or three (A, B, C).
_VALID_OPTION_KEY_SETS = (
    frozenset({"D"}),
    frozenset({"A", "B", "D"}),
    frozenset({"A", "B", "C", "D"}),
)


@dataclass(frozen=True)
class HandoffQuestion:
    question_id: str
    context_summary: str
    question: str
    options: Mapping[str, str]
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not self.question_id or not self.question_id.strip():
            raise ValueError("question_id must be a non-empty string")
        if not isinstance(self.question, str) or not self.question.strip():
            raise ValueError("question must be a non-empty string")
        if not isinstance(self.context_summary, str):
            raise TypeError("context_summary must be a string")
        if _contains_unsafe_content(self.context_summary):
            raise ValueError(
                "context_summary contains a full URL or credential-like "
                "value; sanitize it before constructing this object"
            )
        if not isinstance(self.options, Mapping):
            raise TypeError("options must be a mapping")
        if frozenset(self.options.keys()) not in _VALID_OPTION_KEY_SETS:
            raise ValueError(
                "options must be exactly {D}, {A, B, D}, or {A, B, C, D}"
            )
        if self.options.get("D") != "Do nothing":
            raise ValueError('option D must always be "Do nothing"')
        for key, value in self.options.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"option {key} must be a non-empty string")
            if _contains_unsafe_content(value):
                raise ValueError(
                    f"option {key} contains a full URL or credential-like "
                    "value; sanitize it before constructing this object"
                )
        # Freeze the mapping itself, not just the attribute binding — a
        # frozen dataclass only stops re-assignment, not in-place mutation
        # of a mutable field's contents.
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        if self.expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")

    def is_expired(self, at: datetime) -> bool:
        return at >= self.expires_at

    def as_dict(self) -> dict:
        return {
            "question_id": self.question_id,
            "context_summary": self.context_summary,
            "question": self.question,
            "options": dict(self.options),
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }


_PERMISSION_OPTIONS: Mapping[str, str] = MappingProxyType({"YES": "Yes", "NO": "No"})

# Master Spec section 14 — the exact required CODEX_DATA prompt text.
CODEX_DATA_PROMPT = (
    "This task will use Codex CLI and may send relevant files from the "
    "duplicate workspace to the configured model service. Allow?"
)


@dataclass(frozen=True)
class PermissionQuestion:
    """A CODEX_DATA or PACKAGE_INSTALL authorization prompt.

    Distinct from ``HandoffQuestion``: options are always exactly YES/NO,
    never A/B/C/D, and — for PACKAGE_INSTALL — this object carries the
    exact package names and argv being requested so the Terminal prompt (and
    the eventual authorization check) can display/verify precisely what was
    approved.
    """

    question_id: str
    permission_kind: PermissionKind
    context_summary: str
    prompt: str
    created_at: datetime
    expires_at: datetime
    duplicate_path: str = ""
    package_names: tuple[str, ...] = ()
    proposed_argv: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.question_id or not self.question_id.strip():
            raise ValueError("question_id must be a non-empty string")
        if not isinstance(self.duplicate_path, str) or not self.duplicate_path.strip():
            raise ValueError(
                "duplicate_path must be a non-empty string -- every permission "
                "question is bound to the exact duplicate it was asked about"
            )
        if _contains_unsafe_content(self.duplicate_path):
            raise ValueError(
                "duplicate_path contains a full URL or credential-like value"
            )
        if not isinstance(self.permission_kind, PermissionKind):
            raise TypeError("permission_kind must be a PermissionKind")
        if not isinstance(self.context_summary, str):
            raise TypeError("context_summary must be a string")
        if _contains_unsafe_content(self.context_summary):
            raise ValueError(
                "context_summary contains a full URL or credential-like "
                "value; sanitize it before constructing this object"
            )
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if _contains_unsafe_content(self.prompt):
            raise ValueError(
                "prompt contains a full URL or credential-like value; "
                "sanitize it before constructing this object"
            )
        if not isinstance(self.package_names, tuple) or not all(
            isinstance(item, str) for item in self.package_names
        ):
            raise TypeError("package_names must be a tuple of strings")
        if not isinstance(self.proposed_argv, tuple) or not all(
            isinstance(item, str) for item in self.proposed_argv
        ):
            raise TypeError("proposed_argv must be a tuple of strings")
        for item in (*self.package_names, *self.proposed_argv):
            if _contains_unsafe_content(item):
                raise ValueError(
                    "package_names/proposed_argv must not contain a full "
                    "URL or credential-like value"
                )
        if self.permission_kind is PermissionKind.PACKAGE_INSTALL:
            if not self.package_names:
                raise ValueError(
                    "PACKAGE_INSTALL permission questions require at least "
                    "one package name"
                )
            if not self.proposed_argv:
                raise ValueError(
                    "PACKAGE_INSTALL permission questions require the "
                    "exact proposed argv"
                )
        else:
            if self.package_names or self.proposed_argv:
                raise ValueError(
                    "package_names/proposed_argv only apply to "
                    "PACKAGE_INSTALL permission questions"
                )
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        if self.expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")

    @property
    def options(self) -> Mapping[str, str]:
        """Always exactly YES/NO — never A/B/C/D."""

        return _PERMISSION_OPTIONS

    def is_expired(self, at: datetime) -> bool:
        return at >= self.expires_at

    def as_dict(self) -> dict:
        return {
            "question_id": self.question_id,
            "permission_kind": self.permission_kind.value,
            "context_summary": self.context_summary,
            "prompt": self.prompt,
            "options": dict(self.options),
            "duplicate_path": self.duplicate_path,
            "package_names": list(self.package_names),
            "proposed_argv": list(self.proposed_argv),
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }
