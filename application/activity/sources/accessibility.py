from collections.abc import Callable
from datetime import datetime
import socket

from application.activity.models import ActivityObservation
from utils.time_utils import utc_now


class MacOSAccessibilitySource:
    def __init__(
        self,
        provider,
        bucket_id: str | None = None,
        min_interval_seconds: float = 15.0,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.provider = provider
        self.bucket_id = bucket_id or (
            f"ai-desk-accessibility-{socket.gethostname()}"
        )
        self.min_interval_seconds = min_interval_seconds
        self.clock = clock
        self._last_capture: datetime | None = None

    def start(self) -> None:
        self._last_capture = None

    def stop(self) -> None:
        return None

    def capture(self) -> ActivityObservation | None:
        now = self.clock()
        if (
            self._last_capture is not None
            and (now - self._last_capture).total_seconds()
            < self.min_interval_seconds
        ):
            return None
        # Throttle failed permission/API attempts as well as successful reads.
        self._last_capture = now
        context = self.provider.capture()
        return ActivityObservation(
            timestamp=now,
            bucket_id=self.bucket_id,
            event_type="accessibility.context",
            source="macos_accessibility",
            data=context.as_payload(),
        )
