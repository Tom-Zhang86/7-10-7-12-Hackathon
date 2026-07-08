from collections.abc import Iterator
from contextlib import contextmanager
import sqlite3
from pathlib import Path


class Database:
    """SQLite connection factory and schema initializer."""

    def __init__(self, db_path: str | Path = "ai_desk_presence.db") -> None:
        self.db_path = Path(db_path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Create a SQLite connection with useful defaults enabled."""

        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def close(self) -> None:
        """Close database resources.

        Connections are short-lived and closed after each operation, so this is
        intentionally a no-op for callers that need an explicit cleanup hook.
        """

    def initialize(self) -> None:
        """Create all first-stage tables if they do not exist."""

        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_time TEXT NOT NULL,
                    end_time TEXT,
                    duration_seconds INTEGER NOT NULL DEFAULT 0,
                    break_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS breaks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT,
                    duration_seconds INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (session_id)
                        REFERENCES sessions (id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS context_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER,
                    captured_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id)
                        REFERENCES sessions (id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_start_time
                    ON sessions (start_time);

                CREATE INDEX IF NOT EXISTS idx_breaks_session_id
                    ON breaks (session_id);

                CREATE INDEX IF NOT EXISTS idx_context_events_session_id
                    ON context_events (session_id);

                CREATE INDEX IF NOT EXISTS idx_context_events_captured_at
                    ON context_events (captured_at);
                """
            )
            self._allow_sessionless_context_events(connection)

    @staticmethod
    def _allow_sessionless_context_events(connection: sqlite3.Connection) -> None:
        columns = connection.execute("PRAGMA table_info(context_events)").fetchall()
        session_id_column = next(
            (column for column in columns if column["name"] == "session_id"),
            None,
        )
        if session_id_column is None or int(session_id_column["notnull"]) == 0:
            return

        connection.executescript(
            """
            ALTER TABLE context_events RENAME TO context_events_old;

            CREATE TABLE context_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                captured_at TEXT NOT NULL,
                source TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id)
                    REFERENCES sessions (id)
                    ON DELETE CASCADE
            );

            INSERT INTO context_events (
                id,
                session_id,
                captured_at,
                source,
                payload_json,
                created_at
            )
            SELECT
                id,
                session_id,
                captured_at,
                source,
                payload_json,
                created_at
            FROM context_events_old;

            DROP TABLE context_events_old;

            CREATE INDEX IF NOT EXISTS idx_context_events_session_id
                ON context_events (session_id);

            CREATE INDEX IF NOT EXISTS idx_context_events_captured_at
                ON context_events (captured_at);
            """
        )
