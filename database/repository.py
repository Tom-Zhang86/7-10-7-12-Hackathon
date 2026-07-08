import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional

from database.connection import Database
from models.context_event import ContextEventRecord
from models.session_record import BreakRecord, SessionRecord
from utils.time_utils import parse_datetime


class SessionRepository:
    """Persistence operations for sessions and breaks."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create_session(self, start_time: datetime) -> SessionRecord:
        now_iso = start_time.isoformat()
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO sessions (start_time, created_at, updated_at)
                VALUES (?, ?, ?)
                """,
                (start_time.isoformat(), now_iso, now_iso),
            )
            session_id = int(cursor.lastrowid)
        return self.get_session(session_id)

    def get_session(self, session_id: int) -> SessionRecord:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()

        if row is None:
            raise ValueError(f"Session {session_id} does not exist.")
        return self._row_to_session(row)

    def get_active_session(self) -> Optional[SessionRecord]:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM sessions
                WHERE end_time IS NULL
                ORDER BY start_time DESC
                LIMIT 1
                """
            ).fetchone()

        return self._row_to_session(row) if row else None

    def finish_session(
        self,
        session_id: int,
        end_time: datetime,
        duration_seconds: int,
        break_count: int,
    ) -> SessionRecord:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET end_time = ?,
                    duration_seconds = ?,
                    break_count = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    end_time.isoformat(),
                    duration_seconds,
                    break_count,
                    end_time.isoformat(),
                    session_id,
                ),
            )
        return self.get_session(session_id)

    def update_session_progress(
        self,
        session_id: int,
        duration_seconds: int,
        break_count: int,
        updated_at: datetime,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET duration_seconds = ?,
                    break_count = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    duration_seconds,
                    break_count,
                    updated_at.isoformat(),
                    session_id,
                ),
            )

    def create_break(
        self,
        session_id: int,
        start_time: datetime,
    ) -> BreakRecord:
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO breaks (
                    session_id,
                    start_time,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    session_id,
                    start_time.isoformat(),
                    start_time.isoformat(),
                    start_time.isoformat(),
                ),
            )
            break_id = int(cursor.lastrowid)
        return self.get_break(break_id)

    def get_break(self, break_id: int) -> BreakRecord:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM breaks WHERE id = ?",
                (break_id,),
            ).fetchone()

        if row is None:
            raise ValueError(f"Break {break_id} does not exist.")
        return self._row_to_break(row)

    def get_open_break(self, session_id: int) -> Optional[BreakRecord]:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM breaks
                WHERE session_id = ? AND end_time IS NULL
                ORDER BY start_time DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()

        return self._row_to_break(row) if row else None

    def finish_break(self, break_id: int, end_time: datetime) -> BreakRecord:
        break_record = self.get_break(break_id)
        duration_seconds = int(
            (end_time - break_record.start_time).total_seconds()
        )

        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE breaks
                SET end_time = ?,
                    duration_seconds = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    end_time.isoformat(),
                    max(duration_seconds, 0),
                    end_time.isoformat(),
                    break_id,
                ),
            )
        return self.get_break(break_id)

    def list_sessions_for_day(self, target_date: date) -> list[SessionRecord]:
        start, end = self._day_bounds(target_date)
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM sessions
                WHERE start_time >= ? AND start_time < ?
                ORDER BY start_time ASC
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()

        return [self._row_to_session(row) for row in rows]

    def list_breaks_for_session(self, session_id: int) -> list[BreakRecord]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM breaks
                WHERE session_id = ?
                ORDER BY start_time ASC
                """,
                (session_id,),
            ).fetchall()

        return [self._row_to_break(row) for row in rows]

    def list_breaks_for_day(self, target_date: date) -> list[BreakRecord]:
        start, end = self._day_bounds(target_date)
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM breaks
                WHERE start_time >= ? AND start_time < ?
                ORDER BY start_time ASC
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()

        return [self._row_to_break(row) for row in rows]

    def count_breaks_for_session(self, session_id: int) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM breaks WHERE session_id = ?",
                (session_id,),
            ).fetchone()

        return int(row["count"])

    def create_context_event(
        self,
        session_id: Optional[int],
        source: str,
        payload: dict[str, Any],
        captured_at: datetime,
    ) -> ContextEventRecord:
        payload_json = json.dumps(payload, sort_keys=True)
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO context_events (
                    session_id,
                    captured_at,
                    source,
                    payload_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    captured_at.isoformat(),
                    source,
                    payload_json,
                    captured_at.isoformat(),
                ),
            )
            context_event_id = int(cursor.lastrowid)

        return self.get_context_event(context_event_id)

    def get_context_event(self, context_event_id: int) -> ContextEventRecord:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM context_events WHERE id = ?",
                (context_event_id,),
            ).fetchone()

        if row is None:
            raise ValueError(f"Context event {context_event_id} does not exist.")
        return self._row_to_context_event(row)

    def list_context_events_for_day(
        self,
        target_date: date,
    ) -> list[ContextEventRecord]:
        start, end = self._day_bounds(target_date)
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM context_events
                WHERE captured_at >= ? AND captured_at < ?
                ORDER BY captured_at ASC
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()

        return [self._row_to_context_event(row) for row in rows]

    @staticmethod
    def _row_to_session(row) -> SessionRecord:
        return SessionRecord(
            id=int(row["id"]),
            start_time=parse_datetime(row["start_time"]),
            end_time=parse_datetime(row["end_time"])
            if row["end_time"]
            else None,
            duration_seconds=int(row["duration_seconds"]),
            break_count=int(row["break_count"]),
        )

    @staticmethod
    def _row_to_break(row) -> BreakRecord:
        return BreakRecord(
            id=int(row["id"]),
            session_id=int(row["session_id"]),
            start_time=parse_datetime(row["start_time"]),
            end_time=parse_datetime(row["end_time"])
            if row["end_time"]
            else None,
            duration_seconds=int(row["duration_seconds"]),
        )

    @staticmethod
    def _row_to_context_event(row) -> ContextEventRecord:
        return ContextEventRecord(
            id=int(row["id"]),
            session_id=int(row["session_id"])
            if row["session_id"] is not None
            else None,
            timestamp=parse_datetime(row["captured_at"]),
            source=str(row["source"]),
            payload=json.loads(row["payload_json"]),
            payload_json=str(row["payload_json"]),
        )

    @staticmethod
    def _day_bounds(target_date: date) -> tuple[datetime, datetime]:
        start = datetime.combine(target_date, time.min, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        return start, end
