from collections.abc import Callable
from dataclasses import dataclass
import platform
import subprocess
from typing import Protocol


class ContextCaptureError(RuntimeError):
    """Raised when macOS desktop context cannot be captured."""


@dataclass(frozen=True)
class DesktopContext:
    app: str
    window_title: str

    def as_payload(self) -> dict[str, str]:
        return {
            "app": self.app,
            "window_title": self.window_title,
        }


class CommandResult(Protocol):
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[list[str], float], CommandResult]


def _run_command(command: list[str], timeout: float) -> CommandResult:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


class MacOSContextProvider:
    """Capture the frontmost macOS app and window title via System Events."""

    _DELIMITER = "\x1f"
    _SCRIPT = """
tell application "System Events"
    set frontProcess to first application process whose frontmost is true
    set appName to name of frontProcess
    set windowTitle to ""
    try
        set windowTitle to name of front window of frontProcess
    end try
    return appName & (ASCII character 31) & windowTitle
end tell
""".strip()

    def __init__(
        self,
        runner: CommandRunner = _run_command,
        timeout_seconds: float = 2.0,
        platform_name: str | None = None,
    ) -> None:
        self.runner = runner
        self.timeout_seconds = timeout_seconds
        self.platform_name = platform_name or platform.system()

    def capture(self) -> DesktopContext:
        if self.platform_name != "Darwin":
            raise ContextCaptureError("macOS context capture requires Darwin.")

        try:
            result = self.runner(
                ["/usr/bin/osascript", "-e", self._SCRIPT],
                self.timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ContextCaptureError(f"osascript failed: {exc}") from exc

        if result.returncode != 0:
            detail = result.stderr.strip() or "unknown osascript error"
            raise ContextCaptureError(detail)

        raw = result.stdout.rstrip("\r\n")
        app, separator, title = raw.partition(self._DELIMITER)
        if not separator or not app.strip():
            raise ContextCaptureError("osascript returned malformed context.")

        return DesktopContext(
            app=app.strip(),
            window_title=title.strip(),
        )
