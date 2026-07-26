"""Persisted state machine definition (Master Spec section 8).

This module defines the pure transition-rule table only — it performs no
I/O and has no side effects; persistence lives in ``store.py``. Phase 1
implements every Master Spec state and its allowed transitions, but not the
side effects a later phase will attach to each transition (no subprocess
execution, no workspace copying happens here or anywhere in Phase 1).

Codex data-authorization ordering (Phase 1 review correction): Codex CLI
must not start — the state machine must not enter EXECUTING — before a
separate, explicit matching YES on the data-authorization question. The
corrected coding path is therefore
``AUTHORIZED -> WORKSPACE_DUPLICATING -> PERMISSION_PENDING(CODEX_DATA) ->
EXECUTING``, and every ``PERMISSION_PENDING`` row always carries an explicit
``PermissionKind`` — there is no generic/kind-less pending permission.
"""
from __future__ import annotations

from enum import Enum


class OwnerHandoffState(str, Enum):
    OBSERVING = "OBSERVING"
    LEFT_CANDIDATE = "LEFT_CANDIDATE"
    OWNER_LEFT_CONFIRMED = "OWNER_LEFT_CONFIRMED"
    QUESTION_PENDING = "QUESTION_PENDING"
    AUTHORIZED = "AUTHORIZED"
    WORKSPACE_DUPLICATING = "WORKSPACE_DUPLICATING"
    EXECUTING = "EXECUTING"
    PERMISSION_PENDING = "PERMISSION_PENDING"
    RETURN_REQUESTED = "RETURN_REQUESTED"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    RETURNED = "RETURNED"
    CANCELED = "CANCELED"
    FAILED = "FAILED"


class PermissionKind(str, Enum):
    """Recorded on every PERMISSION_PENDING row; never left unspecified."""

    CODEX_DATA = "CODEX_DATA"
    PACKAGE_INSTALL = "PACKAGE_INSTALL"


TERMINAL_STATES = frozenset(
    {
        OwnerHandoffState.RETURNED,
        OwnerHandoffState.CANCELED,
        OwnerHandoffState.FAILED,
    }
)

# States a live subprocess/thread handle could plausibly be attached to.
# Restart recovery (store.py) force-fails any task found in one of these
# states without a live handle, rather than silently resuming it.
# RETURN_REQUESTED is included (Phase 4 repair): it means a
# ReturnCoordinator grace-period/step-completion sequence was in progress
# when the process stopped -- there is no live handle to resume that
# sequence with, so it must fail closed exactly like the execution states,
# not be silently left pending or waved through to READY_FOR_REVIEW.
RUNTIME_BOUND_STATES = frozenset(
    {
        OwnerHandoffState.WORKSPACE_DUPLICATING,
        OwnerHandoffState.EXECUTING,
        OwnerHandoffState.PERMISSION_PENDING,
        OwnerHandoffState.RETURN_REQUESTED,
    }
)

# Allowed source states for each target state. A transition is valid only if
# the task's current persisted state is a member of the set for the
# requested target.
ALLOWED_SOURCES: dict[OwnerHandoffState, frozenset[OwnerHandoffState]] = {
    OwnerHandoffState.OBSERVING: frozenset(
        {
            OwnerHandoffState.LEFT_CANDIDATE,
            OwnerHandoffState.FAILED,
            OwnerHandoffState.CANCELED,
            OwnerHandoffState.RETURNED,
        }
    ),
    OwnerHandoffState.LEFT_CANDIDATE: frozenset({OwnerHandoffState.OBSERVING}),
    OwnerHandoffState.OWNER_LEFT_CONFIRMED: frozenset(
        {OwnerHandoffState.LEFT_CANDIDATE}
    ),
    OwnerHandoffState.QUESTION_PENDING: frozenset(
        {OwnerHandoffState.OWNER_LEFT_CONFIRMED}
    ),
    OwnerHandoffState.AUTHORIZED: frozenset({OwnerHandoffState.QUESTION_PENDING}),
    OwnerHandoffState.WORKSPACE_DUPLICATING: frozenset(
        {OwnerHandoffState.AUTHORIZED}
    ),
    OwnerHandoffState.EXECUTING: frozenset(
        {
            OwnerHandoffState.AUTHORIZED,  # research skill: no duplication needed
            OwnerHandoffState.PERMISSION_PENDING,  # CODEX_DATA or PACKAGE_INSTALL granted
        }
    ),
    OwnerHandoffState.PERMISSION_PENDING: frozenset(
        {
            OwnerHandoffState.WORKSPACE_DUPLICATING,  # requesting CODEX_DATA
            OwnerHandoffState.EXECUTING,  # requesting PACKAGE_INSTALL mid-execution
        }
    ),
    OwnerHandoffState.RETURN_REQUESTED: frozenset({OwnerHandoffState.EXECUTING}),
    OwnerHandoffState.READY_FOR_REVIEW: frozenset(
        {
            OwnerHandoffState.RETURN_REQUESTED,
            OwnerHandoffState.EXECUTING,
        }
    ),
    OwnerHandoffState.RETURNED: frozenset({OwnerHandoffState.READY_FOR_REVIEW}),
    OwnerHandoffState.CANCELED: frozenset(
        {
            OwnerHandoffState.LEFT_CANDIDATE,
            OwnerHandoffState.OWNER_LEFT_CONFIRMED,
            OwnerHandoffState.QUESTION_PENDING,
            OwnerHandoffState.AUTHORIZED,
            OwnerHandoffState.WORKSPACE_DUPLICATING,
            OwnerHandoffState.PERMISSION_PENDING,
        }
    ),
    OwnerHandoffState.FAILED: frozenset(RUNTIME_BOUND_STATES),
}

# When transitioning INTO PERMISSION_PENDING, the source state determines
# which permission_kind must be recorded (Phase 1 review correction) — there
# is no "generic" pending permission.
REQUIRED_PERMISSION_KIND_BY_SOURCE: dict[OwnerHandoffState, PermissionKind] = {
    OwnerHandoffState.WORKSPACE_DUPLICATING: PermissionKind.CODEX_DATA,
    OwnerHandoffState.EXECUTING: PermissionKind.PACKAGE_INSTALL,
}


class TransitionError(Exception):
    """Raised when a requested transition is not structurally valid."""


class PermissionKindError(TransitionError):
    """Raised for any permission_kind rule violation.

    A subclass of ``TransitionError`` so existing ``except TransitionError``
    call sites keep working; distinguished so callers that care specifically
    about permission-kind auditing can catch it precisely.
    """


def is_allowed(source: OwnerHandoffState, target: OwnerHandoffState) -> bool:
    return source in ALLOWED_SOURCES.get(target, frozenset())


def validate_transition(
    *,
    source: OwnerHandoffState,
    target: OwnerHandoffState,
    permission_kind: PermissionKind | None = None,
) -> None:
    """Raise ``TransitionError`` if the requested transition is not allowed.

    Pure, DB-independent structural validation only — does not touch
    persistence or the current "actual" state of any task; callers pass in
    whatever ``source`` they believe the task is in, and ``store.py`` is
    responsible for atomically confirming that belief against the database
    before applying anything.

    This function enforces every ``permission_kind`` rule that does *not*
    require knowing a task's actual persisted pending kind:

    - entering PERMISSION_PENDING requires the one correct kind for that
      source state (Phase 1 review correction);
    - leaving PERMISSION_PENDING (to EXECUTING or CANCELED) requires *some*
      kind be supplied — but whether it is the *right* one can only be
      confirmed against the database, which is
      ``OwnerHandoffStore.apply_transition``'s job via
      ``assert_permission_kind_matches`` below;
    - every other transition must not carry a permission_kind at all — a
      kind on an unrelated transition would be silently-accepted, unaudited
      noise.
    """

    if not is_allowed(source, target):
        raise TransitionError(f"{target.value} may not follow {source.value}")

    entering_permission_pending = target is OwnerHandoffState.PERMISSION_PENDING
    leaving_permission_pending = (
        source is OwnerHandoffState.PERMISSION_PENDING and not entering_permission_pending
    )

    if entering_permission_pending:
        required = REQUIRED_PERMISSION_KIND_BY_SOURCE.get(source)
        if permission_kind is None:
            raise PermissionKindError(
                "permission_kind is required when entering PERMISSION_PENDING"
            )
        if required is not None and permission_kind is not required:
            raise PermissionKindError(
                f"entering PERMISSION_PENDING from {source.value} requires "
                f"permission_kind={required.value}, got {permission_kind.value}"
            )
    elif leaving_permission_pending:
        if permission_kind is None:
            raise PermissionKindError(
                "permission_kind is required when leaving PERMISSION_PENDING"
            )
    else:
        if permission_kind is not None:
            raise PermissionKindError(
                f"{source.value} -> {target.value} does not involve "
                "PERMISSION_PENDING; permission_kind must be None"
            )


def assert_permission_kind_matches(
    *,
    current_permission_kind: PermissionKind | None,
    permission_kind: PermissionKind,
) -> None:
    """Enforce that a transition leaving PERMISSION_PENDING names the kind
    that is actually persisted for the task.

    Called by ``OwnerHandoffStore.apply_transition`` once it has read the
    task's current record — this is the DB-dependent half of permission-kind
    validation that ``validate_transition`` above cannot perform on its own.
    """

    if current_permission_kind is None or permission_kind is not current_permission_kind:
        current_label = (
            current_permission_kind.value if current_permission_kind is not None else "None"
        )
        raise PermissionKindError(
            f"permission_kind {permission_kind.value} does not match the "
            f"task's persisted pending kind ({current_label})"
        )
