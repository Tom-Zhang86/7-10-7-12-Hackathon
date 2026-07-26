"""CODEX_DATA and PACKAGE_INSTALL permission flows (Master Spec section 14,
15).

Repair (Phase 3 gate, permission-forgeability): a granted permit can only
ever be produced by ``PermissionGate.grant_from_answer``, which itself only
ever calls through to ``QuestionLifecycle.submit_answer`` with the raw,
unvalidated ``WearableAnswer`` -- it never accepts a caller-supplied
"already accepted" outcome, and the grant-recording callback only fires
once ``submit_answer`` has *itself* confirmed a real YES and successfully
applied the PERMISSION_PENDING -> EXECUTING transition. Anything needed to
build the grant record (in particular, computing the live duplicate digest)
happens strictly *before* that call, so if it fails, no transition is even
attempted and the task is left exactly where it was -- it can never end up
EXECUTING with a grant that failed to record.

A granted permit is a single-use capability, not a standing authorization:
``PermissionGate.consume`` is the only way to "spend" one, and it does so
atomically (the permit is removed from the gate the instant it is
consumed) immediately before the real process runner is invoked. A permit
only matches the exact task, question, permission kind, session/duplicate,
freshly-recomputed duplicate digest, and (for PACKAGE_INSTALL) exact
package specs/argv it was granted for -- a previous YES can never authorize
a different task, a different duplicate, a modified duplicate, or a
different package command.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from uuid import uuid4

from application.owner_handoff.domain.presence import WearableAnswer
from application.owner_handoff.domain.question import CODEX_DATA_PROMPT, PermissionQuestion
from application.owner_handoff.questions.lifecycle import AnswerSubmissionResult, QuestionLifecycle
from application.owner_handoff.state_machine import (
    OwnerHandoffState,
    PermissionKind,
)
from application.owner_handoff.store import OwnerHandoffStore
from application.owner_handoff.workspace.duplicator import sha256_file
from utils.time_utils import utc_now


class PermissionNotGrantedError(RuntimeError):
    """Raised by ``PermissionGate.consume`` when no permit exists that
    matches the exact task/session/duplicate/digest/command being
    requested, or the matching permit has expired. Callers must treat this
    as fail-closed: never invoke the real runner if this is raised."""


class PermissionContextMismatchError(RuntimeError):
    """Raised by ``PermissionGate.grant_from_answer`` when the caller-
    supplied ``duplicate_path``/``package_specs``/``argv`` do not exactly
    match what the pending ``PermissionQuestion`` actually displayed to the
    owner. Raised BEFORE ``QuestionLifecycle.submit_answer`` is ever
    called, so no state transition is attempted and no permit is ever
    created -- a YES can only ever authorize exactly what was shown."""


def compute_manifest_hash(duplicate_path: Path | str) -> str:
    """Freshly scan and hash the ACTUAL duplicate directory on disk right
    now -- never trust caller-supplied ``FileRecord`` values as proof of
    current state.

    Deterministic hash binding an authorization to this exact, live
    duplicate state (every file currently present, by relative path and
    content hash). Changes for modified files, added files, deleted files,
    renamed files, and path/type changes (e.g. a file replaced by a
    same-named symlink), because each of those changes what this function
    actually observes on disk at call time.
    """

    root = Path(duplicate_path)
    entries: list[str] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries.append(f"{relative}:SYMLINK")
            continue
        if path.is_dir():
            continue
        if not path.is_file():
            continue
        entries.append(f"{relative}:{sha256_file(path)}")
    payload = "|".join(entries)
    return hashlib.sha256(f"{root}|{payload}".encode()).hexdigest()


@dataclass(frozen=True)
class PermissionAuthorization:
    """A single-use permit bound to the exact context it was granted for."""

    permission_kind: PermissionKind
    task_id: str
    question_id: str
    session_id: str
    duplicate_path: str
    duplicate_digest: str
    expires_at: datetime
    granted_at: datetime
    package_specs: tuple[str, ...] = ()
    argv: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.permission_kind, PermissionKind):
            raise TypeError("permission_kind must be a PermissionKind")
        for name in (
            "task_id",
            "question_id",
            "session_id",
            "duplicate_path",
            "duplicate_digest",
        ):
            if not getattr(self, name):
                raise ValueError(f"{name} must be a non-empty string")
        if self.expires_at.tzinfo is None or self.granted_at.tzinfo is None:
            raise ValueError("expires_at and granted_at must be timezone-aware")


class PermissionGate:
    """Binds granted permissions to their exact context, and is the only
    component that can either record a grant or consume one."""

    def __init__(self) -> None:
        self._authorizations: dict[tuple[str, PermissionKind], PermissionAuthorization] = {}

    def build_codex_data_question(
        self,
        *,
        context_summary: str,
        duplicate_path: str,
        expiration_seconds: float,
        clock: Callable[[], datetime] = utc_now,
        question_id_factory: Callable[[], str] | None = None,
    ) -> PermissionQuestion:
        now = clock()
        question_id = (question_id_factory or (lambda: f"permission-{uuid4()}"))()
        return PermissionQuestion(
            question_id=question_id,
            permission_kind=PermissionKind.CODEX_DATA,
            context_summary=context_summary,
            prompt=CODEX_DATA_PROMPT,
            created_at=now,
            expires_at=now + timedelta(seconds=expiration_seconds),
            duplicate_path=duplicate_path,
        )

    def build_package_install_question(
        self,
        *,
        context_summary: str,
        package_names: tuple[str, ...],
        proposed_argv: tuple[str, ...],
        duplicate_path: str,
        expiration_seconds: float,
        clock: Callable[[], datetime] = utc_now,
        question_id_factory: Callable[[], str] | None = None,
    ) -> PermissionQuestion:
        now = clock()
        question_id = (question_id_factory or (lambda: f"permission-{uuid4()}"))()
        prompt = (
            f"Install {', '.join(package_names)} inside the local virtual "
            f"environment at {duplicate_path}? This will not touch any "
            "global environment. Allow?"
        )
        return PermissionQuestion(
            question_id=question_id,
            permission_kind=PermissionKind.PACKAGE_INSTALL,
            context_summary=context_summary,
            prompt=prompt,
            created_at=now,
            expires_at=now + timedelta(seconds=expiration_seconds),
            duplicate_path=duplicate_path,
            package_names=package_names,
            proposed_argv=proposed_argv,
        )

    def grant_from_answer(
        self,
        *,
        lifecycle: QuestionLifecycle,
        answer: WearableAnswer,
        store: OwnerHandoffStore,
        task_id: str,
        session_id: str,
        duplicate_path: str | Path,
        compute_digest: Callable[[], str],
        package_specs: tuple[str, ...] = (),
        argv: tuple[str, ...] = (),
        clock: Callable[[], datetime] = utc_now,
    ) -> AnswerSubmissionResult:
        """The one path from a raw wearable answer to a recorded permission
        grant.

        Never accepts a pre-built "accepted" outcome: ``lifecycle`` performs
        its own validation against the raw ``answer``, and the grant is only
        ever recorded from inside ``submit_answer``'s own
        ``on_permission_granted`` callback -- i.e. only after a real YES has
        survived every rejection check and the PERMISSION_PENDING ->
        EXECUTING transition has actually been committed to the store.

        Repair (bind YES to what the owner actually saw): before anything
        else, the caller-supplied ``duplicate_path``/``package_specs``/
        ``argv`` are checked against the pending ``PermissionQuestion``'s
        own ``duplicate_path``/``package_names``/``proposed_argv``. Any
        mismatch raises ``PermissionContextMismatchError`` immediately --
        before ``compute_digest`` runs, before ``submit_answer`` is called,
        before any state transition is attempted -- so a YES can never be
        stretched to cover a duplicate/package/argv the owner never saw
        displayed. For CODEX_DATA questions, ``package_specs``/``argv`` must
        both be empty (they only apply to PACKAGE_INSTALL).

        ``compute_digest`` (and the ``PermissionAuthorization`` construction
        it feeds) also runs BEFORE ``submit_answer`` is called at all, so if
        it raises, no transition is attempted and the task is left exactly
        where it was -- it can never end up EXECUTING with a grant that
        failed to record.
        """

        pending = lifecycle.pending_question
        prebuilt: PermissionAuthorization | None = None
        if isinstance(pending, PermissionQuestion):
            normalized_specs = tuple(package_specs)
            normalized_argv = tuple(argv)
            if str(duplicate_path) != pending.duplicate_path:
                raise PermissionContextMismatchError(
                    "duplicate_path does not match the pending permission "
                    "question's duplicate_path"
                )
            if pending.permission_kind is PermissionKind.PACKAGE_INSTALL:
                if normalized_specs != pending.package_names:
                    raise PermissionContextMismatchError(
                        "package_specs do not exactly match the pending "
                        "PACKAGE_INSTALL question's package_names"
                    )
                if normalized_argv != pending.proposed_argv:
                    raise PermissionContextMismatchError(
                        "argv does not exactly match the pending "
                        "PACKAGE_INSTALL question's proposed_argv"
                    )
            elif normalized_specs or normalized_argv:
                raise PermissionContextMismatchError(
                    "package_specs/argv must be empty for a CODEX_DATA question"
                )

            digest = compute_digest()
            prebuilt = PermissionAuthorization(
                permission_kind=pending.permission_kind,
                task_id=task_id,
                question_id=pending.question_id,
                session_id=session_id,
                duplicate_path=str(duplicate_path),
                duplicate_digest=digest,
                expires_at=pending.expires_at,
                granted_at=clock(),
                package_specs=normalized_specs,
                argv=normalized_argv,
            )

        def _on_granted(question: PermissionQuestion) -> None:
            # A plain dict assignment of an already-fully-validated object:
            # this cannot fail, by construction.
            if prebuilt is not None:
                self._authorizations[(task_id, question.permission_kind)] = prebuilt

        return lifecycle.submit_answer(
            answer, store=store, task_id=task_id, on_permission_granted=_on_granted
        )

    def consume(
        self,
        *,
        task_id: str,
        permission_kind: PermissionKind,
        session_id: str,
        duplicate_path: str | Path,
        live_digest: str,
        now: datetime,
        question_id: str,
        package_specs: tuple[str, ...] = (),
        argv: tuple[str, ...] = (),
    ) -> PermissionAuthorization:
        """Require and atomically consume exactly one matching permit.

        Must be called immediately before invoking the real process
        runner (Codex CLI or the package installer) -- never after. A
        permit is single-use: once consumed here it is removed and can
        never be consumed again (a second call with identical arguments
        raises exactly like a call that was never granted). ``question_id``
        is mandatory (not merely checked when supplied) -- a permit only
        matches the EXACT task/session/duplicate path/live digest/question/
        (package specs+argv, for PACKAGE_INSTALL) it was granted for;
        anything else -- including an expired permit -- raises
        ``PermissionNotGrantedError`` and the caller must not proceed.
        """

        if not question_id:
            raise ValueError("question_id is required and must be non-empty")

        key = (task_id, permission_kind)
        authorization = self._authorizations.get(key)
        if authorization is None:
            raise PermissionNotGrantedError(
                f"no {permission_kind.value} permit has been granted for task {task_id!r}"
            )

        mismatch = (
            authorization.session_id != session_id
            or authorization.duplicate_path != str(duplicate_path)
            or authorization.duplicate_digest != live_digest
            or authorization.question_id != question_id
            or authorization.package_specs != tuple(package_specs)
            or authorization.argv != tuple(argv)
        )
        if mismatch or now >= authorization.expires_at:
            raise PermissionNotGrantedError(
                f"no matching, unexpired {permission_kind.value} permit for this "
                "exact task/session/duplicate/digest/command"
            )

        # Consume atomically: remove before returning so a second call
        # (even with the exact same arguments) can never succeed again.
        del self._authorizations[key]
        return authorization

    def verify_authorization(
        self,
        *,
        task_id: str,
        permission_kind: PermissionKind,
        session_id: str,
        now: datetime,
        duplicate_path: str | Path | None = None,
        duplicate_digest: str | None = None,
    ) -> bool:
        """Read-only, non-consuming check -- never mutates the gate.

        True only if a grant exists for this EXACT task, kind, and session
        (and, when supplied, the exact duplicate path/digest), and it has
        not expired. A previous YES for a different task/session/duplicate
        never verifies. Intended for inspection/reporting; real execution
        must call ``consume``, not this method.
        """

        authorization = self._authorizations.get((task_id, permission_kind))
        if authorization is None:
            return False
        if authorization.session_id != session_id:
            return False
        if duplicate_path is not None and authorization.duplicate_path != str(duplicate_path):
            return False
        if duplicate_digest is not None and authorization.duplicate_digest != duplicate_digest:
            return False
        if now >= authorization.expires_at:
            return False
        return True

    def revoke(self, *, task_id: str, permission_kind: PermissionKind) -> None:
        self._authorizations.pop((task_id, permission_kind), None)


def request_codex_data_permission(
    store: OwnerHandoffStore, task_id: str, question: PermissionQuestion
) -> None:
    """WORKSPACE_DUPLICATING -> PERMISSION_PENDING(CODEX_DATA)."""

    store.apply_transition(
        task_id=task_id,
        event_id=f"permission-request:{question.question_id}",
        from_state=OwnerHandoffState.WORKSPACE_DUPLICATING,
        to_state=OwnerHandoffState.PERMISSION_PENDING,
        reason="duplicate workspace ready; requesting codex data authorization",
        permission_kind=PermissionKind.CODEX_DATA,
    )


def request_package_install_permission(
    store: OwnerHandoffStore, task_id: str, question: PermissionQuestion
) -> None:
    """EXECUTING -> PERMISSION_PENDING(PACKAGE_INSTALL)."""

    store.apply_transition(
        task_id=task_id,
        event_id=f"permission-request:{question.question_id}",
        from_state=OwnerHandoffState.EXECUTING,
        to_state=OwnerHandoffState.PERMISSION_PENDING,
        reason="install permission requested",
        permission_kind=PermissionKind.PACKAGE_INSTALL,
    )
