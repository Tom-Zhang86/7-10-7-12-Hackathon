"""One deny-by-default execution policy layer (Master Spec section 12-16).

Every check here is called BEFORE any subprocess runner (fake or real) is
ever invoked — a violation raises ``ExecutionPolicyViolation`` and the
caller must not proceed to run anything. This is the single place all of
Phase 3's executors (Codex CLI, package installer) consult for the
"always prohibited" list; it does not itself run anything.
"""
from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from application.owner_handoff.domain.work_context import redact_sensitive


class ExecutionPolicyViolation(RuntimeError):
    """Raised for any denied action. The message names the specific rule;
    it never includes the offending secret value itself."""


# Shell metacharacters / composition operators that indicate an
# untrusted/constructed shell string rather than a plain argv list.
_SHELL_METACHARACTERS = ("&&", "||", ";", "|", "`", "$(", ">", "<")

_ESCALATION_TOKENS = frozenset({"sudo", "runas", "doas"})

_GIT_REMOTE_VERBS = frozenset({"push", "fetch", "pull", "clone", "remote"})

_EXTERNAL_ACTION_TOOLS = (
    "curl",
    "wget",
    "scp",
    "rsync",
    "ssh",
    "npm publish",
    "twine upload",
    "docker push",
    "mail",
    "sendmail",
    "smtp",
    "slack",
)

_PACKAGE_MANAGER_TOOLS = frozenset({"brew", "npm", "yarn", "apt", "apt-get", "yum", "dnf"})


def _normalize_executable_token(token: str) -> str:
    """Normalize one argv token to the bare, lowercased executable name it
    would resolve to, so ``/usr/bin/git``, ``git``, ``git.exe``, and
    ``C:\\Program Files\\Git\\git.EXE`` are all recognized as the same
    executable. Uses ``os.path.basename`` (not ``pathlib``, which is
    platform-dependent about backslash handling) so an absolute Windows
    path supplied while running on POSIX -- or vice versa -- is still
    normalized correctly."""

    # basename only understands the current platform's separator by
    # default; split on both explicitly so this is platform-independent.
    tail = token.replace("\\", "/").rsplit("/", 1)[-1]
    lowered = tail.lower()
    if lowered.endswith(".exe"):
        lowered = lowered[: -len(".exe")]
    return lowered


class ExecutionPolicy:
    """Stateless collection of deny-by-default checks."""

    def check_argv_is_list(self, argv: object) -> None:
        if isinstance(argv, str):
            raise ExecutionPolicyViolation("argv must be a list, never a shell string")
        if not isinstance(argv, Sequence) or not argv:
            raise ExecutionPolicyViolation("argv must be a non-empty list")
        for item in argv:
            if not isinstance(item, str) or not item:
                raise ExecutionPolicyViolation(
                    "every argv item must be a non-empty string"
                )

    def check_no_shell_true(self, shell: bool) -> None:
        if shell:
            raise ExecutionPolicyViolation("shell=True is never permitted")

    def check_no_shell_metacharacters(self, argv: Sequence[str]) -> None:
        for item in argv:
            for token in _SHELL_METACHARACTERS:
                if token in item:
                    raise ExecutionPolicyViolation(
                        f"argv item contains a shell metacharacter ({token!r}); "
                        "arbitrary shell command strings are prohibited"
                    )

    def check_no_escalation(self, argv: Sequence[str]) -> None:
        for item in argv:
            if _normalize_executable_token(item) in _ESCALATION_TOKENS:
                raise ExecutionPolicyViolation("sudo/admin escalation is never permitted")

    def check_no_git_remote_operations(self, argv: Sequence[str]) -> None:
        if not argv:
            return
        if _normalize_executable_token(argv[0]) == "git":
            for verb in argv[1:]:
                if verb.lower() in _GIT_REMOTE_VERBS:
                    raise ExecutionPolicyViolation(
                        f"git {verb} (remote operation) is prohibited"
                    )

    def check_no_external_action_tools(self, argv: Sequence[str]) -> None:
        joined = " ".join(argv).lower()
        for tool in _EXTERNAL_ACTION_TOOLS:
            if tool in joined:
                raise ExecutionPolicyViolation(
                    f"prohibited external-action tool detected: {tool!r}"
                )

    def check_no_uncontrolled_package_manager(self, argv: Sequence[str]) -> None:
        normalized = {_normalize_executable_token(item) for item in argv}
        matched = normalized & _PACKAGE_MANAGER_TOOLS
        if matched:
            raise ExecutionPolicyViolation(
                "only the controlled local-venv Python installer is permitted; "
                f"brew/npm/apt/global package managers are prohibited: {sorted(matched)}"
            )

    def check_no_secrets_in_argv(self, argv: Sequence[str]) -> None:
        for item in argv:
            if redact_sensitive(item) != item.strip():
                raise ExecutionPolicyViolation(
                    "argv must not contain a secret/token/password-like value"
                )

    def check_path_within_duplicate(self, path: Path, duplicate_root: Path) -> None:
        resolved = Path(path).resolve()
        resolved_root = Path(duplicate_root).resolve()
        if resolved != resolved_root and resolved_root not in resolved.parents:
            raise ExecutionPolicyViolation(
                f"path is outside the duplicate workspace: {path}"
            )

    def check_cwd_is_duplicate(self, cwd: Path, duplicate_root: Path) -> None:
        if Path(cwd).resolve() != Path(duplicate_root).resolve():
            raise ExecutionPolicyViolation("cwd must be exactly the duplicate workspace root")

    def check_argv_safe_for_subprocess(
        self, argv: Sequence[str], *, shell: bool = False
    ) -> None:
        """Run the full battery of argv-level checks used before invoking
        any subprocess (Codex CLI or the package installer)."""

        self.check_argv_is_list(argv)
        self.check_no_shell_true(shell)
        self.check_no_shell_metacharacters(argv)
        self.check_no_escalation(argv)
        self.check_no_git_remote_operations(argv)
        self.check_no_external_action_tools(argv)
        self.check_no_uncontrolled_package_manager(argv)
        self.check_no_secrets_in_argv(argv)
