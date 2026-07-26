import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from application.owner_handoff.domain.execution import ExecutionResult, ExecutionStatus
from application.owner_handoff.domain.manifest import DuplicationManifest, FileRecord
from application.owner_handoff.persistence import TaskArtifactStore
from application.owner_handoff.workspace.path_policy import PathPolicyError

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


class TaskArtifactStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.session_root = Path(self.temp_dir.name) / "sessions"
        self.store = TaskArtifactStore(session_root=self.session_root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_artifacts_are_written_under_session_root_only(self) -> None:
        self.store.save_selected_task("task-1", selected_task="Fix the bug", skill="coding")
        task_dir = self.store.task_dir("task-1")
        self.assertTrue(str(task_dir.resolve()).startswith(str(self.session_root.resolve())))
        self.assertTrue((task_dir / "selected_task.json").exists())

    def test_unsafe_task_id_rejected(self) -> None:
        with self.assertRaises(PathPolicyError):
            self.store.save_selected_task("../escape", selected_task="x", skill="coding")

    def test_execution_result_secrets_are_redacted(self) -> None:
        result = ExecutionResult(
            status=ExecutionStatus.COMPLETED,
            summary="done: token=SUPERSECRET123abc",
            detail={"message": "api_key=ABCDEFGHIJKL should not appear"},
        )
        self.store.save_execution_result("task-1", label="coding", result=result)
        raw = (self.store.task_dir("task-1") / "execution_result_coding.json").read_text()
        self.assertNotIn("SUPERSECRET123abc", raw)
        self.assertNotIn("ABCDEFGHIJKL", raw)

    def test_final_report_round_trip(self) -> None:
        from application.owner_handoff.domain.resume import ResumeReport

        report = ResumeReport(
            selected_task="Fix the bug", work_completed=("done",), files_created=("a.py",),
            files_modified_in_duplicate=(), original_files_modified=(), packages_installed=(),
            status="ready_for_review",
        )
        self.store.save_final_report("task-1", report)
        loaded = self.store.load_final_report("task-1")
        self.assertEqual(loaded["selected_task"], "Fix the bug")
        self.assertEqual(loaded["status"], "ready_for_review")

    def test_load_final_report_returns_none_when_absent(self) -> None:
        self.assertIsNone(self.store.load_final_report("never-saved"))

    def test_packages_installed_round_trip(self) -> None:
        self.store.save_packages_installed("task-1", ("requests==2.31.0",))
        self.assertEqual(
            self.store.load_packages_installed("task-1"), ("requests==2.31.0",)
        )

    def test_write_is_atomic_no_tmp_file_left_behind(self) -> None:
        self.store.save_selected_task("task-1", selected_task="x", skill="coding")
        task_dir = self.store.task_dir("task-1")
        tmp_files = list(task_dir.glob("*.tmp"))
        self.assertEqual(tmp_files, [])

    def test_manifest_persistence_never_writes_into_source(self) -> None:
        manifest = DuplicationManifest(
            session_id="s1", task_id="task-1", source_path="/some/source",
            duplicate_path="/some/duplicate", copied_relative_paths=("main.py",), excluded=(),
            original_files=(FileRecord("main.py", "a" * 64, 1),), post_copy_hashes=(),
            created_at=NOW,
        )
        self.store.save_manifest("task-1", manifest)
        # Nothing under /some/source was ever touched -- everything landed
        # under session_root.
        for path in self.session_root.rglob("*"):
            self.assertNotIn("some", str(path).replace(str(self.session_root), ""))


if __name__ == "__main__":
    unittest.main()
