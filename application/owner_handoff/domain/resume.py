"""Resume report contract (Master Spec section 17-18).

External shape is fixed exactly as specified by the orchestrator/return
coordinator spec — never rename, add, or remove top-level keys, since a
downstream reviewer (a person, not this codebase) reads this verbatim.
Nothing here ever merges or copies duplicate-workspace changes back into
the original; this is a read-only report of what happened.

Repair (fail closed on verification failure): a ``ResumeReport`` may only
ever represent a run whose original workspace was freshly re-verified as
unchanged -- ``original_files_modified`` is therefore enforced to be empty
at construction time. If ``verify_original_unchanged`` finds any
modification, the caller (``ReturnCoordinator``) must never construct a
``ResumeReport`` at all; it must instead produce a
``SafetyFailureRecord`` (see ``return_coordinator.py``) and transition the
task to FAILED. A ``ResumeReport`` existing at all is itself evidence that
verification passed -- there is no "failed" status value this object can
carry.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResumeReport:
    selected_task: str
    work_completed: tuple[str, ...]
    files_created: tuple[str, ...]
    files_modified_in_duplicate: tuple[str, ...]
    original_files_modified: tuple[str, ...]
    packages_installed: tuple[str, ...]
    status: str

    def __post_init__(self) -> None:
        if not isinstance(self.selected_task, str):
            raise TypeError("selected_task must be a string")
        if not isinstance(self.status, str) or not self.status:
            raise ValueError("status must be a non-empty string")
        for name in (
            "work_completed",
            "files_created",
            "files_modified_in_duplicate",
            "original_files_modified",
            "packages_installed",
        ):
            value = getattr(self, name)
            if not isinstance(value, tuple) or not all(isinstance(item, str) for item in value):
                raise TypeError(f"{name} must be a tuple of strings")
        if self.original_files_modified != ():
            raise ValueError(
                "a ResumeReport may only be constructed after original-workspace "
                "verification passed with no modifications -- construct a "
                "SafetyFailureRecord instead when verification fails"
            )

    def as_dict(self) -> dict:
        """Exactly the required external shape -- key set and order fixed."""

        return {
            "selected_task": self.selected_task,
            "work_completed": list(self.work_completed),
            "files_created": list(self.files_created),
            "files_modified_in_duplicate": list(self.files_modified_in_duplicate),
            "original_files_modified": list(self.original_files_modified),
            "packages_installed": list(self.packages_installed),
            "status": self.status,
        }


@dataclass(frozen=True)
class SafetyFailureRecord:
    """Produced instead of a ``ResumeReport`` when original-workspace
    verification fails. Deliberately a different, incompatible shape (not
    a ``ResumeReport`` with a "failed" status) so no caller can mistake one
    for the other or accidentally present a safety failure as a normal,
    reviewable result."""

    selected_task: str
    reason: str
    original_files_modified: tuple[str, ...]
    status: str = "failed_verification"

    def __post_init__(self) -> None:
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("reason must be a non-empty string")
        if not isinstance(self.original_files_modified, tuple) or not all(
            isinstance(item, str) for item in self.original_files_modified
        ):
            raise TypeError("original_files_modified must be a tuple of strings")

    def as_dict(self) -> dict:
        return {
            "selected_task": self.selected_task,
            "reason": self.reason,
            "original_files_modified": list(self.original_files_modified),
            "status": self.status,
        }
