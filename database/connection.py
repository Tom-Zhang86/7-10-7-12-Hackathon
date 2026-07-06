import sqlite3
from pathlib import Path


class Database:
    """SQLite connection factory and schema initializer."""

    def __init__(self, db_path: str | Path = "ai_desk_presence.db") -> None:
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        """Create a SQLite connection with useful defaults enabled."""

        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

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
                    session_id INTEGER NOT NULL,
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
                """
            )
