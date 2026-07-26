import tempfile
import unittest
from pathlib import Path

from database.connection import Database
from database.repository import SessionRepository
from utils.time_utils import utc_now


class DatabaseCleanupTest(unittest.TestCase):
    def test_sqlite_file_can_be_deleted_after_close(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "cleanup.db"
            database = Database(db_path)
            database.initialize()
            repository = SessionRepository(database)
            repository.create_context_event(
                session_id=None,
                source="cleanup_test",
                payload={"ok": True},
                captured_at=utc_now(),
            )

            database.close()
            db_path.unlink()

            self.assertFalse(db_path.exists())


if __name__ == "__main__":
    unittest.main()
