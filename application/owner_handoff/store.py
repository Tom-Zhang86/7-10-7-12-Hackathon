"""Persisted store for the owner-handoff state machine (Master Spec section 8).

Follows the same pattern as ``application/handoff/store.py``: a dedicated
SQLite database, WAL mode, and atomic ``UPDATE ... WHERE state = ?`` compare-
and-swap for every transition. No connection is held open beyond a single
call, so the database file remains deletable immediately after any method
returns (including on Windows).
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
import sqlite3

from application.owner_handoff.state_machine import (
    RUNTIME_BOUND_STATES,
    OwnerHandoffState,
    PermissionKind,
    assert_permission_kind_matches,
    validate_transition,
)
from utils.time_utils import utc_now


class StaleTransitionError(Exception):
    """Raised when a transition's expected source state no longer matches
    the task's actual persisted state (a stale or racing event)."""


class EventConflictError(Exception):
    """Raised when an ``event_id`` is reused for a different transition."""


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    state: OwnerHandoffState
    permission_kind: PermissionKind | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class TransitionEvent:
    event_id: str
    task_id: str
    from_state: OwnerHandoffState
    to_state: OwnerHandoffState
    permission_kind: PermissionKind | None
    reason: str
    occurred_at: datetime


def _permission_value(kind: PermissionKind | None) -> str | None:
    return kind.value if kind is not None else None


class OwnerHandoffStore:
    """SQLite-backed persistence for owner-handoff task state.

    ``occurred_at`` semantics: it is a store-assigned timestamp recorded the
    *first* time an event_id is written, not caller identity data used for
    comparison. A replay of the same semantic event (same task_id,
    from_state, to_state, permission_kind, and reason) is idempotent
    regardless of what ``occurred_at`` value — or lack of one — the replay
    call supplies; the originally recorded ``occurred_at`` is never
    overwritten. Only (task_id, from_state, to_state, permission_kind,
    reason) identify a semantic event for conflict-detection purposes.
    """

    def __init__(self, path: str | Path = "data/owner_handoff.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS owner_handoff_tasks (
                    task_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    permission_kind TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS owner_handoff_events (
                    event_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    from_state TEXT NOT NULL,
                    to_state TEXT NOT NULL,
                    permission_kind TEXT,
                    reason TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_owner_handoff_events_task "
                "ON owner_handoff_events (task_id, occurred_at)"
            )

    def close(self) -> None:
        """No-op: no connection is held open between calls.

        Present so callers have an explicit, symmetric lifecycle method;
        the SQLite file is already safe to delete after any other method on
        this class returns.
        """

    def create_task(self, task_id: str) -> TaskRecord:
        """Create a new task. Always starts at OBSERVING.

        Phase 1 repair: this method previously accepted an arbitrary
        ``initial_state``, which let callers fabricate a task already
        sitting in, say, EXECUTING or an invalid kind-less
        PERMISSION_PENDING — entirely bypassing the state machine's
        transition rules and its permission_kind invariants. Every task now
        starts at OBSERVING and must be driven through
        ``apply_transition`` to reach any other state, the same as
        production code would have to.
        """

        now = utc_now().isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO owner_handoff_tasks "
                "(task_id, state, permission_kind, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, OwnerHandoffState.OBSERVING.value, None, now, now),
            )
            return self._task_record(connection, task_id)

    def get_task(self, task_id: str) -> TaskRecord:
        with self._connect() as connection:
            return self._task_record(connection, task_id)

    def list_tasks_in_state(self, state: OwnerHandoffState) -> list[TaskRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM owner_handoff_tasks WHERE state = ?",
                (state.value,),
            ).fetchall()
        return [self._task_record_from_row(row) for row in rows]

    def history(self, task_id: str) -> list[TransitionEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM owner_handoff_events "
                "WHERE task_id = ? ORDER BY occurred_at, event_id",
                (task_id,),
            ).fetchall()
        return [self._event_record(row) for row in rows]

    def apply_transition(
        self,
        *,
        task_id: str,
        event_id: str,
        from_state: OwnerHandoffState,
        to_state: OwnerHandoffState,
        reason: str,
        permission_kind: PermissionKind | None = None,
        occurred_at: datetime | None = None,
    ) -> TaskRecord:
        """Apply one transition.

        Idempotent on ``event_id``: replaying the same event_id with the
        same transition data (task_id, from_state, to_state,
        permission_kind, reason) is a no-op that returns the current record
        without a second history row. Reusing an ``event_id`` for different
        transition data raises ``EventConflictError``. A transition whose
        expected ``from_state`` no longer matches the task's actual
        persisted state raises ``StaleTransitionError``. A transition
        leaving PERMISSION_PENDING whose supplied ``permission_kind``
        doesn't match the task's actual persisted pending kind raises
        ``PermissionKindError`` (a ``TransitionError`` subclass).
        """

        # Pure, DB-independent structural checks fail fast, before any
        # connection is opened (allowed source/target pairing and the
        # permission_kind rules that don't require reading the database).
        validate_transition(
            source=from_state, target=to_state, permission_kind=permission_kind
        )
        now = (occurred_at or utc_now()).isoformat()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")

            existing_event = connection.execute(
                "SELECT * FROM owner_handoff_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if existing_event is not None:
                same_transition = (
                    existing_event["task_id"] == task_id
                    and existing_event["from_state"] == from_state.value
                    and existing_event["to_state"] == to_state.value
                    and existing_event["permission_kind"]
                    == _permission_value(permission_kind)
                    and existing_event["reason"] == reason
                )
                if not same_transition:
                    raise EventConflictError(
                        f"event_id {event_id!r} was already recorded for a "
                        "different transition"
                    )
                return self._task_record(connection, task_id)

            task_row = connection.execute(
                "SELECT * FROM owner_handoff_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if task_row is None:
                raise KeyError(task_id)

            current_state = OwnerHandoffState(task_row["state"])
            if current_state is not from_state:
                raise StaleTransitionError(
                    f"task {task_id} is in {current_state.value}, "
                    f"not {from_state.value}"
                )

            current_kind = (
                PermissionKind(task_row["permission_kind"])
                if task_row["permission_kind"]
                else None
            )

            leaving_permission_pending = (
                from_state is OwnerHandoffState.PERMISSION_PENDING
                and to_state is not OwnerHandoffState.PERMISSION_PENDING
            )
            if leaving_permission_pending:
                # permission_kind is guaranteed non-None here by
                # validate_transition above; confirm it matches what is
                # actually persisted rather than trusting the caller.
                assert_permission_kind_matches(
                    current_permission_kind=current_kind,
                    permission_kind=permission_kind,
                )
                recorded_kind = current_kind
                new_persisted_kind: PermissionKind | None = None
            elif to_state is OwnerHandoffState.PERMISSION_PENDING:
                recorded_kind = permission_kind
                new_persisted_kind = permission_kind
            else:
                recorded_kind = None
                new_persisted_kind = None

            changed = connection.execute(
                "UPDATE owner_handoff_tasks "
                "SET state = ?, permission_kind = ?, updated_at = ? "
                "WHERE task_id = ? AND state = ?",
                (
                    to_state.value,
                    _permission_value(new_persisted_kind),
                    now,
                    task_id,
                    from_state.value,
                ),
            ).rowcount
            if changed != 1:
                # Another writer changed the state between our SELECT and
                # this UPDATE; treat as stale rather than silently retrying.
                raise StaleTransitionError(
                    f"task {task_id} state changed concurrently"
                )

            connection.execute(
                "INSERT INTO owner_handoff_events "
                "(event_id, task_id, from_state, to_state, permission_kind, "
                "reason, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    task_id,
                    from_state.value,
                    to_state.value,
                    _permission_value(recorded_kind),
                    reason,
                    now,
                ),
            )
            return self._task_record(connection, task_id)

    def recover_after_restart(
        self,
        *,
        reason: str = "process restarted mid-execution",
        event_id_factory: Callable[[TaskRecord], str] | None = None,
    ) -> list[TaskRecord]:
        """Force any runtime-bound task without a live handle to FAILED.

        Must be called explicitly by the process that owns runtime handles
        (a later phase's executor/orchestrator) — only that caller knows
        which handles are actually still live. Importing this module, or
        constructing a store, never triggers recovery by itself.

        Each recovery event gets a unique, auditable event_id. By default
        the id incorporates the task's current ``updated_at`` (which
        changes every time the task transitions), so the *same* task can
        legitimately be recovered more than once across separate
        EXECUTING/PERMISSION_PENDING lifecycles without event_id collisions
        — Phase 1 repair: the previous scheme keyed only on
        (task_id, state), which collided and silently no-op'd on a second
        genuine recovery of the same task. Pass ``event_id_factory`` for
        fully deterministic ids in tests, independent of timestamps.

        If the task being recovered was in PERMISSION_PENDING, the kind
        that was pending is recorded on the recovery event for audit, and
        cleared from the task's own record (it is no longer pending
        anything once FAILED).
        """

        recovered: list[TaskRecord] = []
        placeholders = ",".join("?" for _ in RUNTIME_BOUND_STATES)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                f"SELECT * FROM owner_handoff_tasks WHERE state IN ({placeholders})",
                tuple(state.value for state in RUNTIME_BOUND_STATES),
            ).fetchall()
            now = utc_now().isoformat()
            for row in rows:
                record = self._task_record_from_row(row)
                old_kind = _permission_value(record.permission_kind)

                if event_id_factory is not None:
                    event_id = event_id_factory(record)
                else:
                    event_id = (
                        f"restart-recovery:{record.task_id}:{record.state.value}:"
                        f"{record.updated_at.isoformat()}"
                    )

                existing_event = connection.execute(
                    "SELECT 1 FROM owner_handoff_events WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
                if existing_event is None:
                    connection.execute(
                        "UPDATE owner_handoff_tasks "
                        "SET state = ?, permission_kind = ?, updated_at = ? "
                        "WHERE task_id = ? AND state = ?",
                        (
                            OwnerHandoffState.FAILED.value,
                            None,
                            now,
                            record.task_id,
                            record.state.value,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO owner_handoff_events "
                        "(event_id, task_id, from_state, to_state, "
                        "permission_kind, reason, occurred_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            event_id,
                            record.task_id,
                            record.state.value,
                            OwnerHandoffState.FAILED.value,
                            old_kind,
                            reason,
                            now,
                        ),
                    )
                recovered.append(self._task_record(connection, record.task_id))
        return recovered

    def _task_record(self, connection: sqlite3.Connection, task_id: str) -> TaskRecord:
        row = connection.execute(
            "SELECT * FROM owner_handoff_tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._task_record_from_row(row)

    @staticmethod
    def _task_record_from_row(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            task_id=row["task_id"],
            state=OwnerHandoffState(row["state"]),
            permission_kind=(
                PermissionKind(row["permission_kind"])
                if row["permission_kind"]
                else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _event_record(row: sqlite3.Row) -> TransitionEvent:
        return TransitionEvent(
            event_id=row["event_id"],
            task_id=row["task_id"],
            from_state=OwnerHandoffState(row["from_state"]),
            to_state=OwnerHandoffState(row["to_state"]),
            permission_kind=(
                PermissionKind(row["permission_kind"])
                if row["permission_kind"]
                else None
            ),
            reason=row["reason"],
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
        )
