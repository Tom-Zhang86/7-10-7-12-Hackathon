import os
import tempfile
import unittest
from pathlib import Path

from application.owner_handoff.workspace.path_policy import (
    PathPolicyError,
    validate_destination_path,
    validate_session_id,
    validate_source_path,
)


class PathPolicyNormalPathTest(unittest.TestCase):
    def test_valid_source_inside_boundary_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            boundary = Path(tmp)
            source = boundary / "project"
            source.mkdir()
            session_root = boundary / "sessions"
            session_root.mkdir()

            resolved = validate_source_path(
                source, allowed_boundary=boundary, session_root=session_root
            )
            self.assertEqual(resolved, source.resolve())

    def test_valid_destination_under_session_root_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            boundary = Path(tmp)
            source = boundary / "project"
            source.mkdir()
            session_root = boundary / "sessions"
            session_root.mkdir()
            destination = session_root / "abc123"

            resolved = validate_destination_path(
                destination, source_resolved=source.resolve(), session_root=session_root
            )
            self.assertEqual(resolved, destination.resolve() if destination.exists() else (session_root.resolve() / "abc123"))


class PathPolicyRejectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.boundary = Path(self.temp_dir.name)
        self.session_root = self.boundary / "sessions"
        self.session_root.mkdir()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_filesystem_root_rejected(self) -> None:
        root = Path(self.boundary.resolve().anchor)
        with self.assertRaises(PathPolicyError):
            validate_source_path(root, allowed_boundary=root, session_root=self.session_root)

    def test_home_directory_rejected(self) -> None:
        with self.assertRaises(PathPolicyError):
            validate_source_path(
                Path.home(), allowed_boundary=Path.home(), session_root=self.session_root
            )

    def test_source_outside_allowed_boundary_rejected(self) -> None:
        source = self.boundary / "project"
        source.mkdir()
        other_boundary = self.boundary / "other"
        other_boundary.mkdir()
        with self.assertRaises(PathPolicyError):
            validate_source_path(
                source, allowed_boundary=other_boundary, session_root=self.session_root
            )

    def test_source_inside_session_root_rejected(self) -> None:
        source = self.session_root / "nested"
        source.mkdir()
        with self.assertRaises(PathPolicyError):
            validate_source_path(
                source, allowed_boundary=self.boundary, session_root=self.session_root
            )

    def test_session_root_inside_source_rejected(self) -> None:
        source = self.boundary  # session_root is boundary/sessions, i.e. inside source==boundary
        with self.assertRaises(PathPolicyError):
            validate_source_path(
                source, allowed_boundary=self.boundary, session_root=self.session_root
            )

    def test_nonexistent_source_rejected(self) -> None:
        with self.assertRaises(PathPolicyError):
            validate_source_path(
                self.boundary / "does-not-exist",
                allowed_boundary=self.boundary,
                session_root=self.session_root,
            )

    def test_source_file_not_directory_rejected(self) -> None:
        file_path = self.boundary / "file.txt"
        file_path.write_text("x")
        with self.assertRaises(PathPolicyError):
            validate_source_path(
                file_path, allowed_boundary=self.boundary, session_root=self.session_root
            )

    def test_symlink_source_rejected(self) -> None:
        real_dir = self.boundary / "real_project"
        real_dir.mkdir()
        link = self.boundary / "linked_project"
        try:
            link.symlink_to(real_dir, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation not permitted in this environment")
        with self.assertRaises(PathPolicyError):
            validate_source_path(
                link, allowed_boundary=self.boundary, session_root=self.session_root
            )

    def test_destination_that_already_exists_rejected(self) -> None:
        source = self.boundary / "project"
        source.mkdir()
        destination = self.session_root / "abc123"
        destination.mkdir()
        with self.assertRaises(PathPolicyError):
            validate_destination_path(
                destination, source_resolved=source.resolve(), session_root=self.session_root
            )

    def test_destination_overlapping_source_rejected(self) -> None:
        source = self.boundary / "project"
        source.mkdir()
        # Destination literally inside the source tree.
        destination = source / "nested_dest"
        with self.assertRaises(PathPolicyError):
            validate_destination_path(
                destination, source_resolved=source.resolve(), session_root=self.session_root
            )

    def test_destination_outside_session_root_rejected(self) -> None:
        source = self.boundary / "project"
        source.mkdir()
        destination = self.boundary / "not_under_sessions"
        with self.assertRaises(PathPolicyError):
            validate_destination_path(
                destination, source_resolved=source.resolve(), session_root=self.session_root
            )

    def test_destination_nested_more_than_one_level_rejected(self) -> None:
        source = self.boundary / "project"
        source.mkdir()
        destination = self.session_root / "nested" / "too-deep"
        with self.assertRaises(PathPolicyError):
            validate_destination_path(
                destination, source_resolved=source.resolve(), session_root=self.session_root
            )


class SessionIdValidationTest(unittest.TestCase):
    def test_safe_session_id_accepted(self) -> None:
        self.assertEqual(validate_session_id("abc123"), "abc123")

    def test_dot_and_dotdot_rejected(self) -> None:
        for value in (".", ".."):
            with self.assertRaises(PathPolicyError):
                validate_session_id(value)

    def test_path_separators_rejected(self) -> None:
        for value in ("a/b", "a\\b", "/abs", "\\abs"):
            with self.assertRaises(PathPolicyError):
                validate_session_id(value)

    def test_empty_and_whitespace_rejected(self) -> None:
        for value in ("", "  ", " abc "):
            with self.assertRaises(PathPolicyError):
                validate_session_id(value)


if __name__ == "__main__":
    unittest.main()
