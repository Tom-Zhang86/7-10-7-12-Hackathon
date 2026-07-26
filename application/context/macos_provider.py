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
    browser_title: str = ""
    browser_url: str = ""

    def as_payload(self) -> dict[str, str]:
        payload = {
            "app": self.app,
            "window_title": self.window_title,
        }
        if self.browser_title:
            payload["browser_title"] = self.browser_title
        if self.browser_url:
            payload["browser_url"] = self.browser_url
        return payload


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
    """Capture the frontmost macOS app, window, and active Chrome tab."""

    _DELIMITER = "\x1f"
    _SCRIPT = """
tell application "System Events"
    set frontProcess to first application process whose frontmost is true
    set appName to name of frontProcess
    set windowTitle to ""
    try
        set windowTitle to name of front window of frontProcess
    end try
end tell
set browserTitle to ""
set browserURL to ""
if appName is "Google Chrome" then
    try
        tell application "Google Chrome"
            if (count of windows) > 0 then
                set browserTitle to title of active tab of front window
                set browserURL to URL of active tab of front window
            end if
        end tell
    end try
end if
return appName & (ASCII character 31) & windowTitle & (ASCII character 31) & browserTitle & (ASCII character 31) & browserURL
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
        fields = raw.split(self._DELIMITER, 3)
        # Accept the older two-field response from custom/test runners.
        if len(fields) == 2:
            fields.extend(["", ""])
        if len(fields) != 4 or not fields[0].strip():
            raise ContextCaptureError("osascript returned malformed context.")

        return DesktopContext(
            app=fields[0].strip(),
            window_title=fields[1].strip(),
            browser_title=fields[2].strip(),
            browser_url=fields[3].strip(),
        )
