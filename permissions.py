from __future__ import annotations

from dataclasses import dataclass
import platform
import subprocess
from typing import Any


@dataclass(frozen=True)
class MacOSPermissionStatus:
    input_monitoring: bool
    accessibility: bool

    @property
    def granted(self) -> bool:
        return self.input_monitoring and self.accessibility


def macos_permission_status() -> MacOSPermissionStatus:
    """Read macOS trust state without displaying a prompt."""

    if platform.system() != "Darwin":
        return MacOSPermissionStatus(True, True)
    try:
        import Quartz

        return MacOSPermissionStatus(
            bool(Quartz.CGPreflightListenEventAccess()),
            bool(Quartz.AXIsProcessTrusted()),
        )
    except (AttributeError, ImportError):
        return MacOSPermissionStatus(False, False)


def request_macos_permissions() -> MacOSPermissionStatus:
    """Trigger official macOS prompts; the user must approve TCC switches."""

    if platform.system() != "Darwin":
        return MacOSPermissionStatus(True, True)
    try:
        import Quartz

        Quartz.CGRequestListenEventAccess()
        options: dict[Any, Any] = {
            Quartz.kAXTrustedCheckOptionPrompt: True,
        }
        Quartz.AXIsProcessTrustedWithOptions(options)
    except (AttributeError, ImportError):
        open_macos_permission_settings("input")
    return macos_permission_status()


def open_macos_permission_settings(permission: str) -> None:
    anchor = {
        "input": "Privacy_ListenEvent",
        "accessibility": "Privacy_Accessibility",
    }.get(permission, "Privacy_ListenEvent")
    subprocess.run(
        [
            "/usr/bin/open",
            (
                "x-apple.systempreferences:"
                f"com.apple.preference.security?{anchor}"
            ),
        ],
        check=False,
    )
