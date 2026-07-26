import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


def _load_demo_module():
    repo_root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "run_owner_handoff_demo", repo_root / "run_owner_handoff_demo.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_owner_handoff_demo"] = module
    spec.loader.exec_module(module)
    return module


class DemoPathSafetyTest(unittest.TestCase):
    """Repair: build_demo_orchestrator must validate every output path
    BEFORE any filesystem mutation (mkdir, SQLite open, manifest write) --
    a rejected configuration must leave the source tree completely
    unchanged."""

    def setUp(self) -> None:
        self.demo = _load_demo_module()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.boundary = Path(self.temp_dir.name)
        self.source = self.boundary / "project"
        self.source.mkdir()
        (self.source / "main.py").write_text("print(1)\n")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _snapshot(self) -> set[str]:
        return {str(p.relative_to(self.source)) for p in self.source.rglob("*")}

    def test_session_root_under_source_rejected_and_source_unchanged(self) -> None:
        before = self._snapshot()
        with self.assertRaises(self.demo.DemoCommandError):
            self.demo.build_demo_orchestrator(
                workspace=self.source,
                session_root=self.source / "sessions",
                db_path=self.boundary / "db.sqlite3",
            )
        self.assertEqual(self._snapshot(), before)
        self.assertFalse((self.source / "sessions").exists())

    def test_db_path_under_source_rejected_and_source_unchanged(self) -> None:
        before = self._snapshot()
        with self.assertRaises(self.demo.DemoCommandError):
            self.demo.build_demo_orchestrator(
                workspace=self.source,
                session_root=self.boundary / "sessions",
                db_path=self.source / "owner.sqlite3",
            )
        self.assertEqual(self._snapshot(), before)
        self.assertFalse((self.source / "owner.sqlite3").exists())

    def test_session_root_equal_to_source_rejected_and_source_unchanged(self) -> None:
        before = self._snapshot()
        with self.assertRaises(self.demo.DemoCommandError):
            self.demo.build_demo_orchestrator(
                workspace=self.source,
                session_root=self.source,
                db_path=self.boundary / "db.sqlite3",
            )
        self.assertEqual(self._snapshot(), before)

    def test_source_under_session_root_rejected_and_source_unchanged(self) -> None:
        session_root = self.boundary / "sessions"
        session_root.mkdir()
        nested_source = session_root / "already_inside"
        nested_source.mkdir()
        (nested_source / "main.py").write_text("print(1)\n")
        before = {str(p.relative_to(nested_source)) for p in nested_source.rglob("*")}

        with self.assertRaises(self.demo.DemoCommandError):
            self.demo.build_demo_orchestrator(
                workspace=nested_source,
                session_root=session_root,
                db_path=self.boundary / "db.sqlite3",
            )
        after = {str(p.relative_to(nested_source)) for p in nested_source.rglob("*")}
        self.assertEqual(after, before)

    def test_no_new_directory_or_file_created_anywhere_inside_source_on_failure(self) -> None:
        """A broader sweep: whatever the rejection reason, nothing new
        appears inside source -- no session dir, no db file, no manifest."""

        before_all = {str(p) for p in self.source.rglob("*")}
        for bad_session_root, bad_db_path in (
            (self.source / "sessions", self.boundary / "db.sqlite3"),
            (self.boundary / "sessions", self.source / "db.sqlite3"),
            (self.source, self.boundary / "db.sqlite3"),
        ):
            with self.assertRaises(self.demo.DemoCommandError):
                self.demo.build_demo_orchestrator(
                    workspace=self.source, session_root=bad_session_root, db_path=bad_db_path,
                )
        after_all = {str(p) for p in self.source.rglob("*")}
        self.assertEqual(after_all, before_all)

    def test_valid_configuration_succeeds(self) -> None:
        ctx = self.demo.build_demo_orchestrator(
            workspace=self.source,
            session_root=self.boundary / "sessions",
            db_path=self.boundary / "db.sqlite3",
        )
        self.assertTrue(ctx.orchestrator is not None)


if __name__ == "__main__":
    unittest.main()
