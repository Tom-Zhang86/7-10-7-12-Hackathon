from collections.abc import Callable
from datetime import datetime
import logging
from threading import Event as ThreadEvent
from threading import Lock, Thread, current_thread
from typing import Any

from application.context.macos_provider import DesktopContext
from utils.time_utils import utc_now

logger = logging.getLogger(__name__)


class ContextCollector:
    """Poll desktop context while Working and persist meaningful changes."""

    def __init__(
        self,
        api: Any,
        provider: Any,
        poll_seconds: float = 5.0,
        heartbeat_seconds: float = 60.0,
        max_title_length: int = 240,
        clock: Callable[[], datetime] = utc_now,
        interface_observer: Any | None = None,
    ) -> None:
        self.api = api
        self.provider = provider
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.max_title_length = max_title_length
        self.clock = clock
        self.interface_observer = interface_observer

        self._stop_signal = ThreadEvent()
        self._lock = Lock()
        self._thread: Thread | None = None
        self._session_id: int | None = None
        self._last_context: DesktopContext | None = None
        self._last_recorded_at: datetime | None = None

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, session_id: int) -> None:
        """Start capture, or update the active session of a running worker."""

        with self._lock:
            self._session_id = session_id
            if self.is_running:
                return
            self._last_context = None
            self._last_recorded_at = None
            self._stop_signal.clear()
            self._thread = Thread(
                target=self._run,
                name="macos-context-collector",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            self._stop_signal.set()

        if thread and thread.is_alive() and thread is not current_thread():
            thread.join(timeout=max(self.poll_seconds + 1, 2))

        with self._lock:
            self._thread = None
            self._session_id = None

    def capture_once(self, session_id: int) -> bool:
        """Capture one sample; return whether it was persisted."""

        context = self.provider.capture()
        context = DesktopContext(
            app=context.app[:120],
            window_title=context.window_title[: self.max_title_length],
            browser_title=context.browser_title[: self.max_title_length],
            browser_url=context.browser_url[:500],
        )
        now = self.clock()
        if self.interface_observer is not None:
            self.interface_observer.observe_interface(context)

        changed = context != self._last_context
        heartbeat_due = (
            self._last_recorded_at is None
            or (now - self._last_recorded_at).total_seconds()
            >= self.heartbeat_seconds
        )
        self._last_context = context
        if not changed and not heartbeat_due:
            return False

        self.api.record_context_event(
            session_id=session_id,
            source=(
                "chrome_active_tab"
                if context.browser_title or context.browser_url
                else "macos_active_window"
            ),
            payload=context.as_payload(),
        )
        self._last_recorded_at = now
        return True

    def _run(self) -> None:
        while not self._stop_signal.is_set():
            with self._lock:
                session_id = self._session_id

            if session_id is not None:
                try:
                    self.capture_once(session_id)
                except Exception:
                    logger.exception("macOS context capture failed.")

            self._stop_signal.wait(self.poll_seconds)
