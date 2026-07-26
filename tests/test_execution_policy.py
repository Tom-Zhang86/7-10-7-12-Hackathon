import tempfile
import unittest
from pathlib import Path

from application.owner_handoff.execution.policy import ExecutionPolicy, ExecutionPolicyViolation


class ExecutionPolicyNormalPathTest(unittest.TestCase):
    def test_safe_argv_passes_every_check(self) -> None:
        policy = ExecutionPolicy()
        with tempfile.TemporaryDirectory() as tmp:
            duplicate = Path(tmp)
            argv = ["codex", "exec", "--sandbox", "workspace-write", "--cd", str(duplicate)]
            policy.check_argv_safe_for_subprocess(argv)
            policy.check_cwd_is_duplicate(duplicate, duplicate)
            policy.check_path_within_duplicate(duplicate / "file.txt", duplicate)


class ExecutionPolicyRejectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = ExecutionPolicy()

    def test_shell_string_rejected(self) -> None:
        with self.assertRaises(ExecutionPolicyViolation):
            self.policy.check_argv_safe_for_subprocess("rm -rf /")

    def test_shell_true_rejected(self) -> None:
        with self.assertRaises(ExecutionPolicyViolation):
            self.policy.check_no_shell_true(True)

    def test_shell_metacharacters_rejected(self) -> None:
        with self.assertRaises(ExecutionPolicyViolation):
            self.policy.check_argv_safe_for_subprocess(["echo", "hi", "&&", "rm", "-rf", "/"])

    def test_command_substitution_rejected(self) -> None:
        with self.assertRaises(ExecutionPolicyViolation):
            self.policy.check_argv_safe_for_subprocess(["echo", "$(whoami)"])

    def test_sudo_rejected(self) -> None:
        with self.assertRaises(ExecutionPolicyViolation):
            self.policy.check_argv_safe_for_subprocess(["sudo", "pip", "install", "x"])

    def test_git_push_rejected(self) -> None:
        with self.assertRaises(ExecutionPolicyViolation):
            self.policy.check_argv_safe_for_subprocess(["git", "push", "origin", "main"])

    def test_git_fetch_and_pull_rejected(self) -> None:
        with self.assertRaises(ExecutionPolicyViolation):
            self.policy.check_argv_safe_for_subprocess(["git", "fetch"])
        with self.assertRaises(ExecutionPolicyViolation):
            self.policy.check_argv_safe_for_subprocess(["git", "pull"])

    def test_publish_and_deploy_tools_rejected(self) -> None:
        for argv in (
            ["curl", "https://example.com"],
            ["npm", "publish"],
            ["twine", "upload", "dist/*"],
            ["docker", "push", "myimage"],
        ):
            with self.assertRaises(ExecutionPolicyViolation):
                self.policy.check_argv_safe_for_subprocess(argv)

    def test_messaging_tools_rejected(self) -> None:
        for argv in (["mail", "-s", "hi", "a@b.com"], ["sendmail", "a@b.com"]):
            with self.assertRaises(ExecutionPolicyViolation):
                self.policy.check_argv_safe_for_subprocess(argv)

    def test_uncontrolled_package_managers_rejected(self) -> None:
        for argv in (["brew", "install", "x"], ["npm", "install", "-g", "x"], ["apt-get", "install", "x"]):
            with self.assertRaises(ExecutionPolicyViolation):
                self.policy.check_no_uncontrolled_package_manager(argv)

    def test_secret_in_argv_rejected(self) -> None:
        with self.assertRaises(ExecutionPolicyViolation):
            self.policy.check_argv_safe_for_subprocess(["curl-like", "token=SUPERSECRET123abc"])

    def test_write_outside_duplicate_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            duplicate = Path(tmp) / "dup"
            duplicate.mkdir()
            outside = Path(tmp) / "other" / "file.txt"
            with self.assertRaises(ExecutionPolicyViolation):
                self.policy.check_path_within_duplicate(outside, duplicate)

    def test_cwd_not_duplicate_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            duplicate = Path(tmp) / "dup"
            duplicate.mkdir()
            other = Path(tmp) / "other"
            other.mkdir()
            with self.assertRaises(ExecutionPolicyViolation):
                self.policy.check_cwd_is_duplicate(other, duplicate)

    def test_empty_argv_rejected(self) -> None:
        with self.assertRaises(ExecutionPolicyViolation):
            self.policy.check_argv_safe_for_subprocess([])

    def test_non_string_and_empty_argv_items_rejected(self) -> None:
        with self.assertRaises(ExecutionPolicyViolation):
            self.policy.check_argv_safe_for_subprocess(["git", 123])
        with self.assertRaises(ExecutionPolicyViolation):
            self.policy.check_argv_safe_for_subprocess(["git", ""])

    def test_absolute_executable_path_sudo_rejected(self) -> None:
        with self.assertRaises(ExecutionPolicyViolation):
            self.policy.check_argv_safe_for_subprocess(["/usr/bin/sudo", "pip", "install", "x"])

    def test_windows_exe_suffix_sudo_rejected(self) -> None:
        with self.assertRaises(ExecutionPolicyViolation):
            self.policy.check_argv_safe_for_subprocess(["sudo.exe", "pip", "install", "x"])

    def test_absolute_git_path_push_rejected(self) -> None:
        with self.assertRaises(ExecutionPolicyViolation):
            self.policy.check_argv_safe_for_subprocess(["/usr/bin/git", "push", "origin", "main"])

    def test_windows_git_exe_push_rejected(self) -> None:
        with self.assertRaises(ExecutionPolicyViolation):
            self.policy.check_argv_safe_for_subprocess(
                ["C:\\Program Files\\Git\\bin\\git.EXE", "push", "origin", "main"]
            )

    def test_windows_npm_cmd_style_path_rejected_via_direct_check(self) -> None:
        with self.assertRaises(ExecutionPolicyViolation):
            self.policy.check_no_uncontrolled_package_manager(
                ["C:\\Program Files\\nodejs\\npm.exe", "install", "-g", "x"]
            )

    def test_argv_safe_for_subprocess_calls_package_manager_check(self) -> None:
        with self.assertRaises(ExecutionPolicyViolation):
            self.policy.check_argv_safe_for_subprocess(["brew", "install", "x"])


if __name__ == "__main__":
    unittest.main()
