"""Physical workspace duplication manifest (Master Spec section 10).

All hashes are SHA-256 of file *contents*; file contents themselves are
never stored here or logged anywhere — only paths, hashes, sizes, and
reasons. ``original_files_modified`` must only ever be read once
``verification_performed`` is True; before that, it is meaningless (not a
false "nothing changed" claim) — see ``verification_performed``'s docstring.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime


@dataclass(frozen=True)
class FileRecord:
    relative_path: str
    sha256: str
    size: int

    def __post_init__(self) -> None:
        if not self.relative_path:
            raise ValueError("relative_path must be a non-empty string")
        if not self.sha256 or len(self.sha256) != 64:
            raise ValueError("sha256 must be a 64-character hex digest")
        if self.size < 0:
            raise ValueError("size must be non-negative")


@dataclass(frozen=True)
class ExcludedPathRecord:
    relative_path: str
    reason: str

    def __post_init__(self) -> None:
        if not self.relative_path:
            raise ValueError("relative_path must be a non-empty string")
        if not self.reason or not self.reason.strip():
            raise ValueError("reason must be a non-empty string")


@dataclass(frozen=True)
class DuplicationManifest:
    session_id: str
    task_id: str
    source_path: str
    duplicate_path: str
    copied_relative_paths: tuple[str, ...]
    excluded: tuple[ExcludedPathRecord, ...]
    original_files: tuple[FileRecord, ...]
    post_copy_hashes: tuple[FileRecord, ...]
    created_at: datetime
    files_created_in_duplicate: tuple[str, ...] = ()
    files_modified_in_duplicate: tuple[str, ...] = ()
    # Set by scan_duplicate_changes() when it finds a change to the
    # DUPLICATE side that the fixed ResumeReport shape has no field for
    # (a deleted/renamed-away copied file, an introduced symlink, or an
    # unsafe file/directory type change) -- the caller must treat this as
    # a safety failure, never a normal ready-for-review report.
    duplicate_unsafe: bool = False
    duplicate_unsafe_reason: str = ""
    # False until verify_original_unchanged() has actually run. Reading
    # original_files_modified before this is True would be treating "not
    # yet checked" as "verified clean" -- exactly the false-empty-report
    # this manifest must never produce.
    verification_performed: bool = False
    verification_passed: bool | None = None
    original_files_modified: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.session_id or not self.task_id:
            raise ValueError("session_id and task_id must be non-empty strings")
        if not self.source_path or not self.duplicate_path:
            raise ValueError("source_path and duplicate_path must be non-empty strings")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        if self.verification_performed and self.verification_passed is None:
            raise ValueError(
                "verification_passed must be set once verification_performed is True"
            )
        if not self.verification_performed and (
            self.verification_passed is not None or self.original_files_modified
        ):
            raise ValueError(
                "verification_passed/original_files_modified must not be set "
                "before verification_performed is True"
            )
        if self.duplicate_unsafe and not self.duplicate_unsafe_reason:
            raise ValueError("duplicate_unsafe_reason must be set when duplicate_unsafe is True")

    def with_duplicate_changes(
        self,
        *,
        created: tuple[str, ...],
        modified: tuple[str, ...],
        unsafe: bool = False,
        unsafe_reason: str = "",
    ) -> "DuplicationManifest":
        return replace(
            self,
            files_created_in_duplicate=created,
            files_modified_in_duplicate=modified,
            duplicate_unsafe=unsafe,
            duplicate_unsafe_reason=unsafe_reason,
        )

    def with_verification(
        self, *, passed: bool, modified_paths: tuple[str, ...]
    ) -> "DuplicationManifest":
        return replace(
            self,
            verification_performed=True,
            verification_passed=passed,
            original_files_modified=modified_paths,
        )

    def as_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "task_id": self.task_id,
            "source_path": self.source_path,
            "duplicate_path": self.duplicate_path,
            "copied_relative_paths": list(self.copied_relative_paths),
            "excluded": [
                {"relative_path": item.relative_path, "reason": item.reason}
                for item in self.excluded
            ],
            "original_files": [
                {"relative_path": r.relative_path, "sha256": r.sha256, "size": r.size}
                for r in self.original_files
            ],
            "post_copy_hashes": [
                {"relative_path": r.relative_path, "sha256": r.sha256, "size": r.size}
                for r in self.post_copy_hashes
            ],
            "created_at": self.created_at.isoformat(),
            "files_created_in_duplicate": list(self.files_created_in_duplicate),
            "files_modified_in_duplicate": list(self.files_modified_in_duplicate),
            "duplicate_unsafe": self.duplicate_unsafe,
            "duplicate_unsafe_reason": self.duplicate_unsafe_reason,
            "verification_performed": self.verification_performed,
            "verification_passed": self.verification_passed,
            "original_files_modified": list(self.original_files_modified),
        }
