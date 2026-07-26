"""Controlled package-installer interface (Master Spec section 15).

Supports only a tightly constrained ``python -m pip install`` invocation
against the local virtual environment inside the duplicate — never a shell
string, never a global interpreter, never a URL/local-path/editable
install/requirements file/command substitution. Everything not explicitly
allowed here is rejected before any runner is ever invoked.
"""
from __future__ import annotations

import platform
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

from application.owner_handoff.execution.codex_cli import CommandResult
from application.owner_handoff.execution.permission import (
    PermissionGate,
    PermissionNotGrantedError,
    compute_manifest_hash,
)
from application.owner_handoff.execution.policy import ExecutionPolicy
from application.owner_handoff.state_machine import PermissionKind
from utils.time_utils import utc_now


class PackageSpecError(ValueError):
    """Raised when a package name/version spec fails the strict parser."""


class PackageInstallRejected(RuntimeError):
    """Raised for any install request that violates the controlled-installer
    policy — never proceeds to invoke a runner."""


_PACKAGE_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*$")
_VERSION_PATTERN = re.compile(r"^[0-9][A-Za-z0-9._-]*$")

_FORBIDDEN_PIP_OPTIONS = frozenset(
    {
        "-r",
        "--requirement",
        "-e",
        "--editable",
        "--index-url",
        "-i",
        "--extra-index-url",
        "--find-links",
        "-f",
    }
)


def parse_package_spec(spec: str) -> tuple[str, str | None]:
    """Strictly parse ``name`` or ``name==1.2.3`` only.

    Rejects URLs, local paths, extras (``pkg[extra]``), version ranges
    other than an exact pin, VCS refs (``@``), whitespace, and any other
    syntax pip would otherwise accept.
    """

    if not isinstance(spec, str) or spec != spec.strip() or not spec:
        raise PackageSpecError("package spec must be a non-empty string with no surrounding whitespace")
    if "://" in spec:
        raise PackageSpecError(f"package spec must not be a URL: {spec!r}")
    if spec.startswith((".", "/", "~", "-")):
        raise PackageSpecError(f"package spec must not be a local path or option: {spec!r}")
    if any(char in spec for char in ("@", " ", "\t", ";", "|", "&", "`", "$")):
        raise PackageSpecError(f"unsupported package spec syntax: {spec!r}")

    if "==" in spec:
        name, _, version = spec.partition("==")
        if not _PACKAGE_NAME_PATTERN.match(name):
            raise PackageSpecError(f"invalid package name: {name!r}")
        if not _VERSION_PATTERN.match(version):
            raise PackageSpecError(f"invalid version pin: {version!r}")
        return name, version

    if not _PACKAGE_NAME_PATTERN.match(spec):
        raise PackageSpecError(f"invalid package name: {spec!r}")
    return spec, None


def venv_python_path(duplicate_path: Path, *, platform_name: str | None = None) -> Path:
    """The one permitted interpreter: the duplicate-local virtual
    environment, never a global interpreter."""

    resolved_platform = platform_name or platform.system()
    if resolved_platform == "Windows":
        return duplicate_path / ".venv" / "Scripts" / "python.exe"
    return duplicate_path / ".venv" / "bin" / "python"


def build_install_argv(
    *, package_specs: list[str], duplicate_path: Path, platform_name: str | None = None
) -> list[str]:
    """Build the exact, minimal, safe argv for installing the given
    packages — raises ``PackageSpecError`` for any spec that doesn't pass
    the strict parser."""

    for spec in package_specs:
        parse_package_spec(spec)
    interpreter = venv_python_path(duplicate_path, platform_name=platform_name)
    return [
        str(interpreter),
        "-m",
        "pip",
        "install",
        "--no-input",
        "--disable-pip-version-check",
        *package_specs,
    ]


class InstallerCommandRunner(Protocol):
    """Boundary for the actual install invocation. Tests inject a fake;
    normal tests never invoke a real package installer."""

    def run(self, argv: list[str], *, cwd: Path) -> CommandResult:
        ...


@dataclass(frozen=True)
class PackageInstallOutcome:
    succeeded: bool
    argv: tuple[str, ...]
    exit_code: int | None
    summary: str


class PackageInstaller:
    """The one controlled path from an approved package request to an
    actual (fake, in tests) pip invocation.

    Repair (permission enforcement): immediately before the process runner
    is ever invoked, this installer freshly hashes the live duplicate and
    requires+consumes exactly one matching PACKAGE_INSTALL permit -- bound
    to the exact package specs and argv being requested -- from the
    supplied ``PermissionGate``. If no such permit exists, installation
    fails closed and the process runner is never called.
    """

    def __init__(
        self,
        *,
        runner: InstallerCommandRunner,
        policy: ExecutionPolicy | None = None,
        platform_name: str | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._runner = runner
        self._policy = policy or ExecutionPolicy()
        self._platform_name = platform_name or platform.system()
        self._clock = clock

    def build_argv(self, *, package_specs: list[str], duplicate_path: Path) -> list[str]:
        """The exact argv this installer would use for ``package_specs`` --
        callers requesting PACKAGE_INSTALL authorization must build the
        proposed argv through this method (not ``build_install_argv``
        directly) so the platform-specific interpreter path bound into the
        permit exactly matches what ``install`` will build at consume
        time."""

        return build_install_argv(
            package_specs=package_specs, duplicate_path=duplicate_path,
            platform_name=self._platform_name,
        )

    def install(
        self,
        *,
        package_specs: list[str],
        duplicate_path: Path,
        task_id: str,
        session_id: str,
        question_id: str,
        permission_gate: PermissionGate,
    ) -> PackageInstallOutcome:
        argv = build_install_argv(
            package_specs=package_specs,
            duplicate_path=duplicate_path,
            platform_name=self._platform_name,
        )

        for item in argv:
            if item in _FORBIDDEN_PIP_OPTIONS:
                raise PackageInstallRejected(f"forbidden pip option: {item}")
        if any(tool in argv[0].lower() for tool in ("brew", "npm", "apt", "yum", "dnf")):
            raise PackageInstallRejected("only a local-venv Python installer is permitted")

        self._policy.check_argv_safe_for_subprocess(argv)
        self._policy.check_cwd_is_duplicate(duplicate_path, duplicate_path)
        self._policy.check_path_within_duplicate(
            venv_python_path(duplicate_path, platform_name=self._platform_name), duplicate_path
        )

        live_digest = compute_manifest_hash(duplicate_path)
        try:
            permission_gate.consume(
                task_id=task_id,
                permission_kind=PermissionKind.PACKAGE_INSTALL,
                session_id=session_id,
                duplicate_path=duplicate_path,
                live_digest=live_digest,
                now=self._clock(),
                question_id=question_id,
                package_specs=tuple(package_specs),
                argv=tuple(argv),
            )
        except PermissionNotGrantedError as exc:
            raise PackageInstallRejected(
                f"no valid PACKAGE_INSTALL permit for this exact command: {exc}"
            ) from exc

        result = self._runner.run(argv, cwd=duplicate_path)
        succeeded = result.returncode == 0
        return PackageInstallOutcome(
            succeeded=succeeded,
            argv=tuple(argv),
            exit_code=result.returncode,
            summary=(
                "package install completed"
                if succeeded
                else f"package install failed (exit {result.returncode})"
            ),
        )


class SubprocessInstallerCommandRunner:
    """Real ``InstallerCommandRunner``: ``shell=False``, argv as a list,
    exact ``cwd``, bounded captured output. Never used by this package's
    own automated tests -- only by real, human-invoked runs."""

    _MAX_CAPTURED_OUTPUT_CHARS = 200_000

    def __init__(self, *, timeout_seconds: float = 300.0) -> None:
        self._timeout_seconds = timeout_seconds

    def run(self, argv: list[str], *, cwd: Path) -> CommandResult:
        result = subprocess.run(
            argv,
            cwd=str(cwd),
            shell=False,
            capture_output=True,
            text=True,
            timeout=self._timeout_seconds,
        )
        return CommandResult(
            returncode=result.returncode,
            stdout=result.stdout[: self._MAX_CAPTURED_OUTPUT_CHARS],
            stderr=result.stderr[: self._MAX_CAPTURED_OUTPUT_CHARS],
        )
