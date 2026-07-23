from collections.abc import Callable
from datetime import datetime
import socket

from application.activity.models import ActivityObservation
from utils.time_utils import utc_now


class MacOSWindowSource:
    """Adapt the existing macOS provider to the internal watcher contract."""

    def __init__(
        self,
        provider,
        bucket_id: str | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.provider = provider
        self.bucket_id = bucket_id or f"ai-desk-window-{socket.gethostname()}"
        self.clock = clock

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def capture(self) -> ActivityObservation:
        context = self.provider.capture()
        return ActivityObservation(
            timestamp=self.clock(),
            bucket_id=self.bucket_id,
            event_type="currentwindow",
            source="macos_active_window",
            data=context.as_payload(),
        )
