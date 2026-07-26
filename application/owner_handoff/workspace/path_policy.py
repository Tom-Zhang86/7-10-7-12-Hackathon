"""Path-safety policy for physical workspace duplication (Master Spec
section 10).

Every check operates on *resolved, absolute* paths — never on raw strings —
specifically to defend against escaping through ``..`` segments, symlink
indirection, or filesystem case-normalization differences (e.g. Windows).
For this hackathon MVP, **all** symlinks are rejected outright, anywhere in
the source path or anywhere inside the tree being walked — there is no
"safe symlink" allowance in Phase 3.
"""
from __future__ import annotations

from pathlib import Path


class PathPolicyError(ValueError):
    """Raised for any path-safety violation. The message always names the
    specific rule that was violated; it never includes file contents."""


def _is_filesystem_root(path: Path) -> bool:
    # A root is its own parent on every platform (POSIX "/", Windows
    # "C:\\" or "\\") -- the same platform-independent check used by
    # config.py's _safe_path.
    return path.parent == path


def _reject_symlinks_in_path(path: Path) -> None:
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise PathPolicyError(f"path contains a symlink: {ancestor}")


def validate_source_path(
    source: Path,
    *,
    allowed_boundary: Path,
    session_root: Path,
) -> Path:
    """Resolve and validate an explicitly supplied source workspace path.

    Returns the resolved, absolute path on success; raises
    ``PathPolicyError`` naming the specific violation otherwise. Never
    silently substitutes a default (cwd, repository root, home) — ``source``
    must always be supplied by the caller.
    """

    if source is None:
        raise PathPolicyError("source workspace path must be explicitly supplied")

    _reject_symlinks_in_path(source)
    try:
        resolved = source.resolve(strict=True)
    except OSError as exc:
        raise PathPolicyError(f"source path does not exist or is inaccessible: {exc}") from exc
    if not resolved.is_dir():
        raise PathPolicyError("source must be an existing directory")

    if _is_filesystem_root(resolved):
        raise PathPolicyError("source must not be a filesystem root")

    home = Path.home().resolve()
    if resolved == home:
        raise PathPolicyError("source must not be the user home directory")

    allowed_boundary_resolved = allowed_boundary.resolve()
    if resolved != allowed_boundary_resolved and allowed_boundary_resolved not in resolved.parents:
        raise PathPolicyError(
            "source is outside the explicitly supplied allowed boundary"
        )

    session_root_resolved = session_root.resolve()
    if resolved == session_root_resolved or session_root_resolved in resolved.parents:
        raise PathPolicyError("source must not be inside workspace_session_root")
    if resolved in session_root_resolved.parents or resolved == session_root_resolved:
        raise PathPolicyError(
            "workspace_session_root must not be inside (or equal to) the source"
        )

    return resolved


def validate_destination_path(
    destination: Path,
    *,
    source_resolved: Path,
    session_root: Path,
) -> Path:
    """Resolve and validate the physical-copy destination.

    The destination must always be a not-yet-existing path directly under
    ``workspace_session_root``.
    """

    session_root_resolved = session_root.resolve()
    # The destination itself does not exist yet, so resolve its parent and
    # rejoin -- resolving a nonexistent leaf directly is unreliable across
    # platforms.
    resolved_parent = destination.parent.resolve()
    resolved = resolved_parent / destination.name

    if resolved.parent != session_root_resolved:
        raise PathPolicyError(
            "destination must be exactly one direct child of workspace_session_root"
        )

    if resolved.exists():
        raise PathPolicyError("destination already exists")

    if resolved == source_resolved or source_resolved in resolved.parents:
        raise PathPolicyError("destination overlaps with source")
    if resolved in source_resolved.parents:
        raise PathPolicyError("source overlaps with destination")

    return resolved


def is_symlink_path(path: Path) -> bool:
    """Used while walking a source tree: any symlink encountered (file or
    directory) is rejected outright for this hackathon MVP."""

    return path.is_symlink()


def validate_demo_output_paths(
    *,
    source: Path,
    allowed_boundary: Path,
    session_root: Path,
    db_path: Path,
) -> tuple[Path, Path, Path, Path]:
    """One pure validation step for every output location a demo/CLI
    entry point derives from a caller-supplied source workspace.

    Performs no filesystem mutation whatsoever -- no ``mkdir``, no SQLite
    connection, no manifest write -- so a rejected configuration leaves
    ``source`` (and everything else) byte-for-byte, tree-for-tree
    unchanged. Must be called, and must succeed, before any of those
    writes happen.

    Reuses ``validate_source_path`` for the source/boundary/session_root
    triangle (filesystem-root/home rejection, boundary containment, and
    the overlap check in *both* directions between source and
    session_root), and additionally requires ``db_path`` be neither equal
    to nor inside ``source``.

    Returns the four resolved paths on success.
    """

    resolved_source = validate_source_path(
        source, allowed_boundary=allowed_boundary, session_root=session_root
    )
    resolved_boundary = allowed_boundary.resolve()
    resolved_session_root = session_root.resolve()

    resolved_db_path = Path(db_path).resolve()
    if resolved_db_path == resolved_source or resolved_source in resolved_db_path.parents:
        raise PathPolicyError("db_path must not be equal to or inside the source workspace")

    return resolved_source, resolved_boundary, resolved_session_root, resolved_db_path


def validate_session_id(session_id: str) -> str:
    """Validate a caller-supplied session id as exactly one safe path
    component.

    Rejects empty values, ``.``/``..``, any path separator (forward or
    backslash -- both are checked regardless of platform so a value crafted
    on one OS cannot escape on another), absolute paths, and any value that
    is not identical to its own ``Path(...).name`` (which would indicate
    nesting, e.g. ``"a/b"`` or a trailing separator). Called before any
    filesystem write derived from the session id.
    """

    if not isinstance(session_id, str) or not session_id or session_id != session_id.strip():
        raise PathPolicyError(
            "session_id must be a non-empty string with no surrounding whitespace"
        )
    if session_id in (".", ".."):
        raise PathPolicyError("session_id must not be '.' or '..'")
    if "/" in session_id or "\\" in session_id:
        raise PathPolicyError("session_id must not contain a path separator")
    if Path(session_id).is_absolute():
        raise PathPolicyError("session_id must not be an absolute path")
    if session_id != Path(session_id).name:
        raise PathPolicyError("session_id must be exactly one path component")
    return session_id
