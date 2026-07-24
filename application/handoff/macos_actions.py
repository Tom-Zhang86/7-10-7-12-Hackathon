from __future__ import annotations

import logging
from pathlib import Path
import platform
import subprocess
from typing import Any, Callable

from application.handoff.models import HandoffRecord
from application.handoff.report import save_report


logger = logging.getLogger(__name__)
CommandRunner = Callable[..., Any]


def _apple_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


class MacOSHandoffActions:
    def __init__(
        self,
        *,
        output_root: str | Path = "data/handoffs",
        auto_open: bool = True,
        runner: CommandRunner = subprocess.run,
        platform_name: str | None = None,
    ) -> None:
        self.output_root = Path(output_root)
        self.auto_open = auto_open
        self.runner = runner
        self.platform_name = platform_name or platform.system()

    def notify(self, title: str, message: str) -> None:
        logger.info("%s: %s", title, message)
        if self.platform_name != "Darwin":
            return
        script = (
            f'display notification "{_apple_string(message)}" '
            f'with title "{_apple_string(title)}"'
        )
        self.runner(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )

    def deliver(self, record: HandoffRecord) -> Path:
        report_path = save_report(record, self.output_root)
        self.notify("AI Desk research handoff", "Research result is ready.")
        if self.auto_open and self.platform_name == "Darwin":
            self.runner(
                ["/usr/bin/open", str(report_path)],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        return report_path
