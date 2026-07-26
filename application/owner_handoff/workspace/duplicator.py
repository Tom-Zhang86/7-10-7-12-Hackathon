"""Physical workspace duplication (Master Spec section 10).

Coding work always uses a physical byte-copy, never a git worktree — a
worktree shares the original repository's object database and index, which
is exactly the kind of shared, mutable coupling to the original this
component exists to avoid. Every file is copied independently and hashed
before and after copying so tampering or a partial/corrupted copy is
detectable, and the *original* workspace is re-hashed after execution to
prove — not merely assert — that nothing in it changed.

Repair (Phase 3 gate): the source tree is walked top-down with excluded
directories pruned in place (never enumerated/descended into), exclusion
name/suffix matching is case-insensitive, and any symlink encountered
anywhere in the tree fails the whole duplication closed with
``PathPolicyError`` rather than being silently recorded as "excluded" while
the rest of the copy proceeds.
"""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from uuid import uuid4

from application.owner_handoff.domain.manifest import (
    DuplicationManifest,
    ExcludedPathRecord,
    FileRecord,
)
from application.owner_handoff.workspace.path_policy import (
    PathPolicyError,
    validate_destination_path,
    validate_session_id,
    validate_source_path,
)
from utils.time_utils import utc_now

# Directory names excluded anywhere in the source tree (compared
# case-insensitively; walking never descends into a matched directory).
_EXCLUDED_DIR_NAMES_LOWER = frozenset(
    name.lower()
    for name in (
        ".git",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        ".cache",
        "node_modules",
        "build",
        "dist",
    )
)
_EXCLUDED_DIR_SUFFIXES_LOWER = (".egg-info",)
_EXCLUDED_FILE_SUFFIXES_LOWER = (".pyc", ".sqlite3", ".db")
# Private-key-like files kept out of the duplicate alongside .env/secrets.
_PRIVATE_KEY_FILE_NAMES_LOWER = frozenset({"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"})
_PRIVATE_KEY_SUFFIXES_LOWER = (".pem", ".key", ".pfx", ".p12")


class WorkspaceDuplicationError(RuntimeError):
    """Raised for any duplication failure, including a post-copy integrity
    mismatch or original-file tampering detected during verification."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Backwards-compatible private alias used elsewhere in this module.
_sha256_file = sha256_file


def _dir_exclusion_reason(name: str, *, session_root_name: str) -> str | None:
    lowered = name.lower()
    if lowered in _EXCLUDED_DIR_NAMES_LOWER:
        return f"excluded directory name: {name}"
    if lowered.endswith(_EXCLUDED_DIR_SUFFIXES_LOWER):
        return "excluded: build artifact directory"
    if lowered.startswith("secrets"):
        return "excluded: secrets-like name"
    if lowered.startswith("credentials"):
        return "excluded: credentials-like name"
    # Prior AI Desk session directories nested inside the source tree (an
    # edge case, but excluded defensively): a directory literally named the
    # same as the configured session root's own directory.
    if name == session_root_name:
        return "excluded: prior AI Desk session directory"
    return None


def _file_exclusion_reason(name: str) -> str | None:
    lowered = name.lower()
    if lowered == ".env" or lowered.startswith(".env."):
        return "excluded: .env file"
    if lowered.startswith("secrets"):
        return "excluded: secrets-like name"
    if lowered.startswith("credentials"):
        return "excluded: credentials-like name"
    if lowered in _PRIVATE_KEY_FILE_NAMES_LOWER:
        return "excluded: private key file"
    if lowered.endswith(_PRIVATE_KEY_SUFFIXES_LOWER):
        return "excluded: private key file"
    if lowered.endswith(_EXCLUDED_FILE_SUFFIXES_LOWER):
        return "excluded: cache/database/bytecode file"
    return None


def _classify_and_collect(
    resolved_source: Path, *, session_root_name: str
) -> tuple[list[ExcludedPathRecord], list[Path]]:
    """Walk ``resolved_source`` top-down, pruning excluded directories in
    place so they (and everything under them) are never descended into or
    individually enumerated.

    Raises ``PathPolicyError`` immediately -- before any file is copied --
    if a symlink (file or directory) is encountered anywhere in the tree.
    """

    excluded: list[ExcludedPathRecord] = []
    to_copy: list[Path] = []

    for dirpath, dirnames, filenames in os.walk(resolved_source, topdown=True, followlinks=False):
        current_dir = Path(dirpath)
        kept_dirnames: list[str] = []
        for dirname in sorted(dirnames):
            child = current_dir / dirname
            relative = child.relative_to(resolved_source)
            if child.is_symlink():
                raise PathPolicyError(f"symlink encountered in source tree: {relative}")
            reason = _dir_exclusion_reason(dirname, session_root_name=session_root_name)
            if reason is not None:
                excluded.append(ExcludedPathRecord(str(relative), reason))
                continue
            kept_dirnames.append(dirname)
        # Mutating dirnames in place is how os.walk(topdown=True) is told
        # not to descend into the removed entries.
        dirnames[:] = kept_dirnames

        for filename in sorted(filenames):
            child = current_dir / filename
            relative = child.relative_to(resolved_source)
            if child.is_symlink():
                raise PathPolicyError(f"symlink encountered in source tree: {relative}")
            reason = _file_exclusion_reason(filename)
            if reason is not None:
                excluded.append(ExcludedPathRecord(str(relative), reason))
                continue
            if not child.is_file():
                excluded.append(ExcludedPathRecord(str(relative), "not a regular file"))
                continue
            to_copy.append(relative)

    to_copy.sort()
    return excluded, to_copy


def duplicate_workspace(
    *,
    source: Path,
    allowed_boundary: Path,
    session_root: Path,
    task_id: str,
    session_id: str | None = None,
) -> tuple[Path, DuplicationManifest]:
    """Create a physical, byte-for-byte copy of ``source`` under
    ``session_root``.

    Returns ``(duplicate_path, manifest)``. Raises ``PathPolicyError`` for
    any path-safety violation (checked before any filesystem write) and
    ``WorkspaceDuplicationError`` if a post-copy integrity check fails.
    Never deletes the duplicate itself -- callers (including tests) own
    that cleanup.
    """

    resolved_source = validate_source_path(
        source, allowed_boundary=allowed_boundary, session_root=session_root
    )
    session_id = validate_session_id(session_id) if session_id is not None else uuid4().hex
    destination = session_root / session_id
    resolved_destination = validate_destination_path(
        destination, source_resolved=resolved_source, session_root=session_root
    )

    session_root_name = session_root.resolve().name

    # Classification (including the symlink fail-closed check) runs to
    # completion, entirely before any write, so a rejected tree never leaves
    # behind a partially-written destination directory.
    excluded, to_copy = _classify_and_collect(
        resolved_source, session_root_name=session_root_name
    )

    original_records = [
        FileRecord(
            relative_path=str(relative),
            sha256=_sha256_file(resolved_source / relative),
            size=(resolved_source / relative).stat().st_size,
        )
        for relative in to_copy
    ]

    resolved_destination.mkdir(parents=True, exist_ok=False)
    post_copy_records: list[FileRecord] = []
    for relative, original in zip(to_copy, original_records):
        src_file = resolved_source / relative
        dst_file = resolved_destination / relative
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)
        digest = _sha256_file(dst_file)
        if digest != original.sha256:
            raise WorkspaceDuplicationError(
                f"copy integrity check failed for {relative}"
            )
        post_copy_records.append(
            FileRecord(relative_path=str(relative), sha256=digest, size=dst_file.stat().st_size)
        )

    manifest = DuplicationManifest(
        session_id=session_id,
        task_id=task_id,
        source_path=str(resolved_source),
        duplicate_path=str(resolved_destination),
        copied_relative_paths=tuple(str(r) for r in to_copy),
        excluded=tuple(excluded),
        original_files=tuple(original_records),
        post_copy_hashes=tuple(post_copy_records),
        created_at=utc_now(),
    )
    return resolved_destination, manifest


def scan_duplicate_changes(manifest: DuplicationManifest) -> DuplicationManifest:
    """Re-scan the duplicate and record which files were created or
    modified relative to the post-copy hashes recorded at duplication
    time -- always a fresh scan, never inferred from what an executor
    claims it did.

    The fixed external ``ResumeReport`` shape has no field for a deleted or
    renamed-away file, and the Master Spec prohibits deleting user-created
    files -- so a copied file that has disappeared (deleted or renamed
    away), a symlink introduced into the duplicate, an unsafe file<->
    directory type change, or a missing duplicate root altogether are never
    silently omitted here. Each sets ``duplicate_unsafe=True`` with a
    human-readable ``duplicate_unsafe_reason``; the caller
    (``OwnerHandoffOrchestrator.finalize_return``) must treat that as a
    safety failure, never a normal ready-for-review report.
    """

    duplicate = Path(manifest.duplicate_path)
    if not duplicate.exists():
        return manifest.with_duplicate_changes(
            created=(), modified=(), unsafe=True,
            unsafe_reason=f"duplicate workspace root is missing: {duplicate}",
        )

    original_by_path = {record.relative_path: record.sha256 for record in manifest.post_copy_hashes}
    copied_paths = set(original_by_path.keys())

    created: list[str] = []
    modified: list[str] = []
    seen_paths: set[str] = set()
    unsafe_reasons: list[str] = []

    for path in sorted(duplicate.rglob("*")):
        relative = str(path.relative_to(duplicate))
        if path.is_symlink():
            unsafe_reasons.append(f"symlink introduced into duplicate: {relative}")
            continue
        if path.is_dir():
            if relative in copied_paths:
                unsafe_reasons.append(f"copied file replaced by a directory: {relative}")
            continue
        if not path.is_file():
            unsafe_reasons.append(f"non-regular file introduced into duplicate: {relative}")
            continue

        seen_paths.add(relative)
        if relative not in copied_paths:
            created.append(relative)
            continue
        if _sha256_file(path) != original_by_path[relative]:
            modified.append(relative)

    for relative in sorted(copied_paths - seen_paths):
        # Already reported (as a type change) if a directory now sits where
        # this file used to be -- otherwise it's a plain deletion/rename-away.
        if not (duplicate / relative).is_dir():
            unsafe_reasons.append(f"copied file deleted or renamed away: {relative}")

    return manifest.with_duplicate_changes(
        created=tuple(created),
        modified=tuple(modified),
        unsafe=bool(unsafe_reasons),
        unsafe_reason="; ".join(unsafe_reasons),
    )


def verify_original_unchanged(manifest: DuplicationManifest) -> DuplicationManifest:
    """Re-hash the original workspace and compare against what was recorded
    at duplication time. Always actually computes hashes -- never assumes
    or defaults to "unchanged".

    Verification scope: every file that a fresh duplication of
    ``manifest.source_path`` would copy today -- i.e. everything not inside
    an excluded directory and not matching an excluded file pattern (see
    ``_classify_and_collect``). This detects modifications and deletions
    (recorded files that changed or disappeared) *and* additions/renames
    (files present now that were not part of the original recorded set --
    a rename appears as one deletion plus one addition, both caught). Files
    inside excluded directories (``.git``, ``.venv``, caches, ...) are
    outside this verification's scope by design, exactly as they were never
    copied to the duplicate in the first place -- this does not claim every
    byte in the entire repository was verified, only the portion that was
    ever eligible to be duplicated.
    """

    source = Path(manifest.source_path)
    recorded = {record.relative_path: record for record in manifest.original_files}
    modified: set[str] = set()

    for relative_path, record in recorded.items():
        file_path = source / relative_path
        if not file_path.exists() or file_path.is_symlink() or not file_path.is_file():
            modified.add(relative_path)
            continue
        try:
            digest = _sha256_file(file_path)
        except OSError:
            modified.add(relative_path)
            continue
        if digest != record.sha256:
            modified.add(relative_path)

    if source.exists():
        try:
            _, current_files = _classify_and_collect(source, session_root_name="")
        except PathPolicyError:
            # A symlink now exists where none did before -- unambiguously a
            # change within the verification scope.
            modified.add("<symlink introduced into source tree>")
        else:
            current_set = {str(relative) for relative in current_files}
            modified.update(current_set - set(recorded.keys()))

    return manifest.with_verification(
        passed=(len(modified) == 0), modified_paths=tuple(sorted(modified))
    )
