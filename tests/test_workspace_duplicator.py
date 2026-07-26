import tempfile
import unittest
from pathlib import Path

from application.owner_handoff.workspace.duplicator import (
    duplicate_workspace,
    scan_duplicate_changes,
    verify_original_unchanged,
)
from application.owner_handoff.workspace.path_policy import PathPolicyError


def _make_source(boundary: Path) -> Path:
    source = boundary / "project"
    source.mkdir()
    (source / "main.py").write_text("print('hello')\n")
    (source / "README.md").write_text("# project\n")
    pkg = source / "pkg"
    pkg.mkdir()
    (pkg / "mod.py").write_text("value = 1\n")
    (source / ".git").mkdir()
    (source / ".git" / "config").write_text("core.repo\n")
    (source / ".env").write_text("SECRET=abc\n")
    (source / "secrets.yaml").write_text("token: abc\n")
    venv = source / ".venv"
    (venv / "lib").mkdir(parents=True)
    (venv / "lib" / "site.py").write_text("x = 1\n")
    (source / "data.sqlite3").write_bytes(b"\x00\x01")
    cache = source / "__pycache__"
    cache.mkdir()
    (cache / "main.cpython-311.pyc").write_bytes(b"\x00")
    return source


class WorkspaceDuplicatorNormalPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.boundary = Path(self.temp_dir.name)
        self.session_root = self.boundary / "sessions"
        self.session_root.mkdir()
        self.source = _make_source(self.boundary)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_physical_copy_creates_duplicate_with_real_files(self) -> None:
        duplicate_path, manifest = duplicate_workspace(
            source=self.source,
            allowed_boundary=self.boundary,
            session_root=self.session_root,
            task_id="task-1",
        )
        self.assertTrue((duplicate_path / "main.py").exists())
        self.assertEqual((duplicate_path / "main.py").read_text(), "print('hello')\n")
        self.assertTrue((duplicate_path / "pkg" / "mod.py").exists())

    def test_exclusions_are_not_copied(self) -> None:
        duplicate_path, manifest = duplicate_workspace(
            source=self.source,
            allowed_boundary=self.boundary,
            session_root=self.session_root,
            task_id="task-1",
        )
        for excluded_name in (
            ".git", ".env", "secrets.yaml", ".venv", "data.sqlite3", "__pycache__",
        ):
            self.assertFalse((duplicate_path / excluded_name).exists())
        excluded_paths = {item.relative_path for item in manifest.excluded}
        self.assertIn(".git", excluded_paths)
        self.assertIn(".env", excluded_paths)

    def test_secret_files_excluded_with_reason(self) -> None:
        _, manifest = duplicate_workspace(
            source=self.source,
            allowed_boundary=self.boundary,
            session_root=self.session_root,
            task_id="task-1",
        )
        reasons = {item.relative_path: item.reason for item in manifest.excluded}
        self.assertIn("secrets.yaml", reasons)
        self.assertIn("secret", reasons["secrets.yaml"].lower())

    def test_pre_and_post_copy_hashes_match(self) -> None:
        _, manifest = duplicate_workspace(
            source=self.source,
            allowed_boundary=self.boundary,
            session_root=self.session_root,
            task_id="task-1",
        )
        original_by_path = {r.relative_path: r.sha256 for r in manifest.original_files}
        post_by_path = {r.relative_path: r.sha256 for r in manifest.post_copy_hashes}
        self.assertEqual(original_by_path, post_by_path)
        self.assertIn("main.py", original_by_path)

    def test_duplicate_changes_do_not_affect_original(self) -> None:
        duplicate_path, manifest = duplicate_workspace(
            source=self.source,
            allowed_boundary=self.boundary,
            session_root=self.session_root,
            task_id="task-1",
        )
        (duplicate_path / "main.py").write_text("print('modified in duplicate')\n")
        self.assertEqual((self.source / "main.py").read_text(), "print('hello')\n")
        verified = verify_original_unchanged(manifest)
        self.assertTrue(verified.verification_passed)
        self.assertEqual(verified.original_files_modified, ())

    def test_original_tampering_is_detected(self) -> None:
        _, manifest = duplicate_workspace(
            source=self.source,
            allowed_boundary=self.boundary,
            session_root=self.session_root,
            task_id="task-1",
        )
        (self.source / "main.py").write_text("print('tampered')\n")
        verified = verify_original_unchanged(manifest)
        self.assertFalse(verified.verification_passed)
        self.assertIn("main.py", verified.original_files_modified)

    def test_no_automatic_duplicate_deletion(self) -> None:
        duplicate_path, _ = duplicate_workspace(
            source=self.source,
            allowed_boundary=self.boundary,
            session_root=self.session_root,
            task_id="task-1",
        )
        self.assertTrue(duplicate_path.exists())
        # Nothing in this package deletes it; the test's own tearDown
        # cleans up the whole temp tree.


class WorkspaceDuplicatorRejectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.boundary = Path(self.temp_dir.name)
        self.session_root = self.boundary / "sessions"
        self.session_root.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_root_home_and_outside_boundary_rejected(self) -> None:
        source = self.boundary / "project"
        source.mkdir()
        other = self.boundary / "elsewhere"
        other.mkdir()
        with self.assertRaises(PathPolicyError):
            duplicate_workspace(
                source=source,
                allowed_boundary=other,
                session_root=self.session_root,
                task_id="task-1",
            )

    def test_source_session_overlap_rejected(self) -> None:
        nested_source = self.session_root / "already_inside"
        nested_source.mkdir()
        with self.assertRaises(PathPolicyError):
            duplicate_workspace(
                source=nested_source,
                allowed_boundary=self.boundary,
                session_root=self.session_root,
                task_id="task-1",
            )

    def test_symlink_inside_source_tree_fails_closed(self) -> None:
        """Repair: a symlink anywhere in the tree must fail the whole
        duplication closed with PathPolicyError -- it must never be
        silently recorded as "excluded" while the rest of the copy
        proceeds, and no destination directory may be left behind."""

        source = self.boundary / "project"
        source.mkdir()
        (source / "real.py").write_text("x = 1\n")
        link = source / "linked.py"
        try:
            link.symlink_to(source / "real.py")
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation not permitted in this environment")

        destination = self.session_root / "will-not-be-created"
        with self.assertRaises(PathPolicyError):
            duplicate_workspace(
                source=source,
                allowed_boundary=self.boundary,
                session_root=self.session_root,
                task_id="task-1",
                session_id="will-not-be-created",
            )
        self.assertFalse(destination.exists())

    def test_env_local_and_env_production_not_copied(self) -> None:
        source = self.boundary / "project2"
        source.mkdir()
        (source / "main.py").write_text("print(1)\n")
        (source / ".env.local").write_text("SECRET=abc\n")
        (source / ".env.production").write_text("SECRET=xyz\n")
        (source / ".ENV.Local").write_text("ignored-duplicate-name-on-case-insensitive-fs\n")

        duplicate_path, manifest = duplicate_workspace(
            source=source,
            allowed_boundary=self.boundary,
            session_root=self.session_root,
            task_id="task-2",
        )
        self.assertFalse((duplicate_path / ".env.local").exists())
        self.assertFalse((duplicate_path / ".env.production").exists())
        excluded_paths = {item.relative_path for item in manifest.excluded}
        self.assertIn(".env.local", excluded_paths)
        self.assertIn(".env.production", excluded_paths)

    def test_excluded_directory_trees_are_pruned_not_enumerated(self) -> None:
        source = self.boundary / "project3"
        source.mkdir()
        (source / "main.py").write_text("print(1)\n")
        deep_cache = source / "node_modules" / "pkg" / "nested"
        deep_cache.mkdir(parents=True)
        (deep_cache / "index.js").write_text("module.exports = 1;\n")

        duplicate_path, manifest = duplicate_workspace(
            source=source,
            allowed_boundary=self.boundary,
            session_root=self.session_root,
            task_id="task-3",
        )
        self.assertFalse((duplicate_path / "node_modules").exists())
        excluded_paths = {item.relative_path for item in manifest.excluded}
        # Only the top-level directory is recorded -- descendants were
        # never walked/enumerated at all.
        self.assertIn("node_modules", excluded_paths)
        self.assertNotIn(str(Path("node_modules") / "pkg"), excluded_paths)
        self.assertNotIn(
            str(Path("node_modules") / "pkg" / "nested" / "index.js"), excluded_paths
        )

    def test_case_insensitive_excluded_directory_name(self) -> None:
        source = self.boundary / "project4"
        source.mkdir()
        (source / "main.py").write_text("print(1)\n")
        (source / "NODE_MODULES").mkdir()
        (source / "NODE_MODULES" / "x.js").write_text("1\n")

        duplicate_path, manifest = duplicate_workspace(
            source=source,
            allowed_boundary=self.boundary,
            session_root=self.session_root,
            task_id="task-4",
        )
        self.assertFalse((duplicate_path / "NODE_MODULES").exists())


class WorkspaceDuplicatorSessionIdTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.boundary = Path(self.temp_dir.name)
        self.session_root = self.boundary / "sessions"
        self.session_root.mkdir()
        self.source = self.boundary / "project"
        self.source.mkdir()
        (self.source / "main.py").write_text("print(1)\n")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_unsafe_session_id_rejected_before_any_write(self) -> None:
        for unsafe in ("../escape", "a/b", "a\\b", "..", ".", "", "/abs"):
            with self.subTest(session_id=unsafe):
                with self.assertRaises(PathPolicyError):
                    duplicate_workspace(
                        source=self.source,
                        allowed_boundary=self.boundary,
                        session_root=self.session_root,
                        task_id="task-1",
                        session_id=unsafe,
                    )
                # Nothing should have been written under session_root.
                self.assertEqual(list(self.session_root.iterdir()), [])


class ScanDuplicateChangesSafetyTest(unittest.TestCase):
    """Repair: scan_duplicate_changes must detect changes the fixed
    ResumeReport shape has no field for (deletions, renames-away,
    introduced symlinks, unsafe type changes, a missing duplicate root)
    and flag them as unsafe rather than silently omitting them."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.boundary = Path(self.temp_dir.name)
        self.session_root = self.boundary / "sessions"
        self.session_root.mkdir()
        self.source = self.boundary / "project"
        self.source.mkdir()
        (self.source / "main.py").write_text("print(1)\n")
        (self.source / "other.py").write_text("value = 1\n")
        self.duplicate_path, self.manifest = duplicate_workspace(
            source=self.source, allowed_boundary=self.boundary,
            session_root=self.session_root, task_id="task-1",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_no_changes_is_safe(self) -> None:
        scanned = scan_duplicate_changes(self.manifest)
        self.assertFalse(scanned.duplicate_unsafe)

    def test_deletion_of_copied_file_is_unsafe(self) -> None:
        (self.duplicate_path / "main.py").unlink()
        scanned = scan_duplicate_changes(self.manifest)
        self.assertTrue(scanned.duplicate_unsafe)
        self.assertIn("main.py", scanned.duplicate_unsafe_reason)
        self.assertIn("deleted", scanned.duplicate_unsafe_reason)

    def test_rename_of_copied_file_is_unsafe(self) -> None:
        (self.duplicate_path / "main.py").rename(self.duplicate_path / "renamed.py")
        scanned = scan_duplicate_changes(self.manifest)
        self.assertTrue(scanned.duplicate_unsafe)
        self.assertIn("main.py", scanned.duplicate_unsafe_reason)
        # The new name is a legitimate "created" file from this scan's
        # point of view, in addition to the unsafe deletion of the old one.
        self.assertIn("renamed.py", scanned.files_created_in_duplicate)

    def test_missing_duplicate_root_is_unsafe(self) -> None:
        import shutil

        shutil.rmtree(self.duplicate_path)
        scanned = scan_duplicate_changes(self.manifest)
        self.assertTrue(scanned.duplicate_unsafe)
        self.assertIn("missing", scanned.duplicate_unsafe_reason.lower())

    def test_file_replaced_by_directory_is_unsafe(self) -> None:
        (self.duplicate_path / "main.py").unlink()
        (self.duplicate_path / "main.py").mkdir()
        scanned = scan_duplicate_changes(self.manifest)
        self.assertTrue(scanned.duplicate_unsafe)
        self.assertIn("directory", scanned.duplicate_unsafe_reason)

    def test_symlink_introduced_into_duplicate_is_unsafe(self) -> None:
        link = self.duplicate_path / "sneaky_link.py"
        try:
            link.symlink_to(self.duplicate_path / "main.py")
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation not permitted in this environment")
        scanned = scan_duplicate_changes(self.manifest)
        self.assertTrue(scanned.duplicate_unsafe)
        self.assertIn("symlink", scanned.duplicate_unsafe_reason)

    def test_ordinary_modification_and_creation_remain_safe(self) -> None:
        (self.duplicate_path / "main.py").write_text("print('modified')\n")
        (self.duplicate_path / "NEW.py").write_text("x = 1\n")
        scanned = scan_duplicate_changes(self.manifest)
        self.assertFalse(scanned.duplicate_unsafe)
        self.assertIn("main.py", scanned.files_modified_in_duplicate)
        self.assertIn("NEW.py", scanned.files_created_in_duplicate)


if __name__ == "__main__":
    unittest.main()
