from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any

from application.handoff.models import (
    HandoffInput,
    HandoffRecord,
    HandoffStatus,
    TaskCapsule,
)
from utils.time_utils import utc_now


class HandoffStore:
    """Independent SQLite queue for A2A handoffs."""

    def __init__(self, path: str | Path = "data/handoffs.sqlite3") -> None:
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
                CREATE TABLE IF NOT EXISTS handoffs (
                    handoff_id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    inputs_json TEXT NOT NULL,
                    expected_output TEXT NOT NULL,
                    agent_skill TEXT NOT NULL,
                    constraints_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    a2a_task_id TEXT NOT NULL DEFAULT '',
                    context_id TEXT NOT NULL DEFAULT '',
                    artifact_json TEXT,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    delegated_at TEXT,
                    completed_at TEXT,
                    returned_at TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_handoffs_status_created "
                "ON handoffs (status, created_at)"
            )

    def create(self, capsule: TaskCapsule) -> HandoffRecord:
        now = utc_now().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO handoffs (
                    handoff_id, goal, inputs_json, expected_output,
                    agent_skill, constraints_json, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    capsule.handoff_id,
                    capsule.goal,
                    json.dumps(
                        [item.as_dict() for item in capsule.inputs],
                        ensure_ascii=False,
                    ),
                    capsule.expected_output,
                    capsule.agent_skill,
                    json.dumps(capsule.constraints, ensure_ascii=False),
                    HandoffStatus.ARMED.value,
                    capsule.created_at.isoformat(),
                    now,
                ),
            )
        return self.get(capsule.handoff_id)

    def get(self, handoff_id: str) -> HandoffRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM handoffs WHERE handoff_id = ?",
                (handoff_id,),
            ).fetchone()
        if row is None:
            raise KeyError(handoff_id)
        return self._record(row)

    def list_all(self) -> list[HandoffRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM handoffs ORDER BY created_at"
            ).fetchall()
        return [self._record(row) for row in rows]

    def claim_next_armed(self) -> HandoffRecord | None:
        now = utc_now().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT handoff_id FROM handoffs
                WHERE status = ?
                ORDER BY created_at
                LIMIT 1
                """,
                (HandoffStatus.ARMED.value,),
            ).fetchone()
            if row is None:
                return None
            changed = connection.execute(
                """
                UPDATE handoffs
                SET status = ?, updated_at = ?, delegated_at = ?
                WHERE handoff_id = ? AND status = ?
                """,
                (
                    HandoffStatus.DELEGATING.value,
                    now,
                    now,
                    row["handoff_id"],
                    HandoffStatus.ARMED.value,
                ),
            ).rowcount
            if changed != 1:
                return None
            selected = connection.execute(
                "SELECT * FROM handoffs WHERE handoff_id = ?",
                (row["handoff_id"],),
            ).fetchone()
        return self._record(selected)

    def mark_running(self, handoff_id: str) -> HandoffRecord:
        return self._transition(
            handoff_id,
            HandoffStatus.RUNNING,
            allowed={HandoffStatus.DELEGATING},
        )

    def complete(
        self,
        handoff_id: str,
        *,
        a2a_task_id: str,
        context_id: str,
        artifact: dict[str, Any],
    ) -> HandoffRecord:
        result_status = str(artifact.get("status") or "failed")
        target = {
            "completed": HandoffStatus.READY,
            "input_required": HandoffStatus.INPUT_REQUIRED,
            "failed": HandoffStatus.FAILED,
        }.get(result_status, HandoffStatus.FAILED)
        now = utc_now().isoformat()
        error = ""
        if target is HandoffStatus.FAILED:
            error = str(
                artifact.get("executive_summary")
                or "Research agent returned an invalid status."
            )
        with self._connect() as connection:
            changed = connection.execute(
                """
                UPDATE handoffs
                SET status = ?, a2a_task_id = ?, context_id = ?,
                    artifact_json = ?, error = ?, updated_at = ?, completed_at = ?
                WHERE handoff_id = ? AND status IN (?, ?)
                """,
                (
                    target.value,
                    a2a_task_id,
                    context_id,
                    json.dumps(artifact, ensure_ascii=False),
                    error,
                    now,
                    now,
                    handoff_id,
                    HandoffStatus.DELEGATING.value,
                    HandoffStatus.RUNNING.value,
                ),
            ).rowcount
        if changed != 1:
            raise RuntimeError(f"handoff {handoff_id} is not running")
        return self.get(handoff_id)

    def mark_failed(self, handoff_id: str, error: str) -> HandoffRecord:
        now = utc_now().isoformat()
        with self._connect() as connection:
            changed = connection.execute(
                """
                UPDATE handoffs
                SET status = ?, error = ?, updated_at = ?, completed_at = ?
                WHERE handoff_id = ? AND status IN (?, ?)
                """,
                (
                    HandoffStatus.FAILED.value,
                    error[:1000],
                    now,
                    now,
                    handoff_id,
                    HandoffStatus.DELEGATING.value,
                    HandoffStatus.RUNNING.value,
                ),
            ).rowcount
        if changed != 1:
            raise RuntimeError(f"handoff {handoff_id} cannot fail from its state")
        return self.get(handoff_id)

    def next_deliverable(self) -> HandoffRecord | None:
        values = (
            HandoffStatus.READY.value,
            HandoffStatus.INPUT_REQUIRED.value,
            HandoffStatus.FAILED.value,
        )
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM handoffs
                WHERE status IN (?, ?, ?)
                ORDER BY completed_at, created_at
                LIMIT 1
                """,
                values,
            ).fetchone()
        return self._record(row) if row is not None else None

    def mark_returned(self, handoff_id: str) -> HandoffRecord:
        now = utc_now().isoformat()
        with self._connect() as connection:
            changed = connection.execute(
                """
                UPDATE handoffs
                SET status = ?, updated_at = ?, returned_at = ?
                WHERE handoff_id = ? AND status IN (?, ?, ?)
                """,
                (
                    HandoffStatus.RETURNED.value,
                    now,
                    now,
                    handoff_id,
                    HandoffStatus.READY.value,
                    HandoffStatus.INPUT_REQUIRED.value,
                    HandoffStatus.FAILED.value,
                ),
            ).rowcount
        if changed != 1:
            raise RuntimeError(f"handoff {handoff_id} is not deliverable")
        return self.get(handoff_id)

    def cancel(self, handoff_id: str) -> HandoffRecord:
        return self._transition(
            handoff_id,
            HandoffStatus.CANCELED,
            allowed={HandoffStatus.ARMED},
        )

    def _transition(
        self,
        handoff_id: str,
        target: HandoffStatus,
        *,
        allowed: set[HandoffStatus],
    ) -> HandoffRecord:
        now = utc_now().isoformat()
        placeholders = ",".join("?" for _ in allowed)
        parameters = [target.value, now, handoff_id]
        parameters.extend(item.value for item in allowed)
        with self._connect() as connection:
            changed = connection.execute(
                f"UPDATE handoffs SET status = ?, updated_at = ? "
                f"WHERE handoff_id = ? AND status IN ({placeholders})",
                parameters,
            ).rowcount
        if changed != 1:
            raise RuntimeError(f"handoff {handoff_id} cannot transition to {target.value}")
        return self.get(handoff_id)

    @staticmethod
    def _record(row: sqlite3.Row) -> HandoffRecord:
        inputs = tuple(
            HandoffInput(str(item["kind"]), str(item["value"]))
            for item in json.loads(row["inputs_json"])
        )
        capsule = TaskCapsule(
            handoff_id=row["handoff_id"],
            goal=row["goal"],
            inputs=inputs,
            expected_output=row["expected_output"],
            agent_skill=row["agent_skill"],
            constraints=json.loads(row["constraints_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
        return HandoffRecord(
            capsule=capsule,
            status=HandoffStatus(row["status"]),
            a2a_task_id=row["a2a_task_id"],
            context_id=row["context_id"],
            artifact=json.loads(row["artifact_json"])
            if row["artifact_json"]
            else None,
            error=row["error"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
            delegated_at=datetime.fromisoformat(row["delegated_at"])
            if row["delegated_at"]
            else None,
            completed_at=datetime.fromisoformat(row["completed_at"])
            if row["completed_at"]
            else None,
            returned_at=datetime.fromisoformat(row["returned_at"])
            if row["returned_at"]
            else None,
        )
