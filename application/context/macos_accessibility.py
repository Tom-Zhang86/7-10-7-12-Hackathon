from collections.abc import Callable
from dataclasses import dataclass
import platform
import subprocess

from application.context.macos_provider import (
    CommandResult,
    ContextCaptureError,
)


@dataclass(frozen=True)
class AccessibilityContext:
    app: str
    window_title: str
    texts: tuple[str, ...]

    def as_payload(self) -> dict:
        return {
            "app": self.app,
            "window_title": self.window_title,
            "accessibility_text": list(self.texts),
            "content_source": "accessibility",
        }


def _run_command(command: list[str], timeout: float) -> CommandResult:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


class MacOSAccessibilityProvider:
    """Read bounded labels from the focused window without screenshots/OCR."""

    _FIELD_SEPARATOR = "\x1f"
    _TEXT_SEPARATOR = "\x1e"
    _SCRIPT = """
tell application "System Events"
    set frontProcess to first application process whose frontmost is true
    set appName to name of frontProcess
    set windowTitle to ""
    set collectedTexts to {}
    try
        set frontWindow to front window of frontProcess
        set windowTitle to name of frontWindow
        set allItems to entire contents of frontWindow
        repeat with itemRef in allItems
            if (count of collectedTexts) is greater than or equal to 80 then exit repeat
            try
                set itemRole to role of itemRef
                if itemRole is "AXStaticText" or itemRole is "AXHeading" or itemRole is "AXLink" then
                    set itemName to name of itemRef as text
                    if itemName is not "" and itemName is not "missing value" then
                        set end of collectedTexts to itemName
                    end if
                end if
            end try
        end repeat
    end try
    set oldDelimiters to AppleScript's text item delimiters
    set AppleScript's text item delimiters to ASCII character 30
    set joinedText to collectedTexts as text
    set AppleScript's text item delimiters to oldDelimiters
    return appName & (ASCII character 31) & windowTitle & (ASCII character 31) & joinedText
end tell
""".strip()

    def __init__(
        self,
        runner: Callable[[list[str], float], CommandResult] = _run_command,
        timeout_seconds: float = 4.0,
        platform_name: str | None = None,
    ) -> None:
        self.runner = runner
        self.timeout_seconds = timeout_seconds
        self.platform_name = platform_name or platform.system()

    def capture(self) -> AccessibilityContext:
        if self.platform_name != "Darwin":
            raise ContextCaptureError("macOS accessibility capture requires Darwin.")
        try:
            result = self.runner(
                ["/usr/bin/osascript", "-e", self._SCRIPT],
                self.timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ContextCaptureError(f"accessibility capture failed: {exc}") from exc
        if result.returncode != 0:
            raise ContextCaptureError(
                result.stderr.strip() or "unknown accessibility error"
            )
        parts = result.stdout.rstrip("\r\n").split(self._FIELD_SEPARATOR, 2)
        if len(parts) != 3 or not parts[0].strip():
            raise ContextCaptureError("accessibility capture returned malformed data")
        texts = tuple(
            dict.fromkeys(
                text.strip()[:200]
                for text in parts[2].split(self._TEXT_SEPARATOR)
                if text.strip()
            )
        )
        return AccessibilityContext(
            app=parts[0].strip(),
            window_title=parts[1].strip(),
            texts=texts[:80],
        )
