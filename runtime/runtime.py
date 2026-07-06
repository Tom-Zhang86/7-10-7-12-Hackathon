from collections.abc import Callable
from datetime import datetime
from queue import Empty, Queue
from threading import Event as ThreadEvent
from threading import Thread
from typing import Optional

from events.dispatcher import EventDispatcher, EventListener
from events.event_types import (
    BreakEnded,
    BreakStarted,
    Event,
    PresenceDetected,
    PresenceLost,
    SessionEnded,
    SessionStarted,
    Shutdown,
    StateChanged,
    StatisticsUpdated,
)
from models.session_record import BreakRecord, SessionRecord
from models.state import PresenceState
from services.stats_service import StatsService
from session.manager import SessionManager
from utils.time_utils import utc_now


class Runtime:
    """Long-running event loop for the AI Desk Presence system layer."""

    def __init__(
        self,
        session_manager: SessionManager,
        stats_service: StatsService,
        dispatcher: Optional[EventDispatcher] = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.session_manager = session_manager
        self.stats_service = stats_service
        self.dispatcher = dispatcher or EventDispatcher()
        self.clock = clock

        self._queue: Queue[Event] = Queue()
        self._stop_signal = ThreadEvent()
        self._thread: Optional[Thread] = None
        self._running = False

    @property
    def is_running(self) -> bool:
        """Return whether the runtime loop is active."""

        return self._running

    def start(self, block: bool = False) -> None:
        """Start the runtime loop.

        When block=False, the loop runs in a background thread. When block=True,
        this call runs the loop on the current thread until Shutdown/stop.
        """

        if self._running:
            return

        self._stop_signal.clear()
        self._running = True

        if block:
            self._run_loop()
            return

        self._thread = Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Request graceful shutdown and wait for the loop to stop."""

        if not self._running:
            return

        self.post_event(Shutdown(timestamp=self.clock()))

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def post_event(self, event: Event) -> None:
        """Add an input event to the runtime queue."""

        self._queue.put(event)

    def wait_until_idle(self) -> None:
        """Block until all queued events have been processed."""

        self._queue.join()

    def subscribe(self, event_name: str, listener: EventListener) -> None:
        """Subscribe a listener to runtime events."""

        self.dispatcher.subscribe(event_name, listener)

    def unsubscribe(self, event_name: str, listener: EventListener) -> None:
        """Remove a runtime event listener."""

        self.dispatcher.unsubscribe(event_name, listener)

    def _run_loop(self) -> None:
        while not self._stop_signal.is_set():
            try:
                event = self._queue.get(timeout=0.2)
            except Empty:
                continue

            self._handle_event(event)
            self._queue.task_done()

        self._running = False

    def _handle_event(self, event: Event) -> None:
        self.dispatcher.publish(event)

        if isinstance(event, PresenceDetected):
            self._handle_presence_detected()
        elif isinstance(event, PresenceLost):
            self._handle_presence_lost()
        elif isinstance(event, Shutdown):
            self._handle_shutdown()

    def _handle_presence_detected(self) -> None:
        old_state = self.session_manager.state

        if old_state == PresenceState.IDLE:
            session = self.session_manager.start_work()
            self._publish_state_change(old_state)
            self._publish_session_started(session)
            self._publish_statistics_updated()
        elif old_state == PresenceState.BREAK:
            break_record = self.session_manager.end_break()
            self._publish_state_change(old_state)
            self._publish_break_ended(break_record)
            self._publish_statistics_updated()

    def _handle_presence_lost(self) -> None:
        old_state = self.session_manager.state

        if old_state == PresenceState.WORKING:
            break_record = self.session_manager.start_break()
            self._publish_state_change(old_state)
            self._publish_break_started(break_record)
            self._publish_statistics_updated()

    def _handle_shutdown(self) -> None:
        old_state = self.session_manager.state

        if old_state == PresenceState.BREAK:
            break_record = self.session_manager.end_break()
            self._publish_state_change(old_state)
            self._publish_break_ended(break_record)
            old_state = self.session_manager.state

        if self.session_manager.state == PresenceState.WORKING:
            session = self.session_manager.end_work()
            self._publish_state_change(old_state)
            self._publish_session_ended(session)
            self._publish_statistics_updated()
        elif self.session_manager.state == PresenceState.IDLE:
            self.session_manager.finish_day()
            self._publish_state_change(old_state)

        self._stop_signal.set()

    def _publish_state_change(self, old_state: PresenceState) -> None:
        new_state = self.session_manager.state
        if old_state == new_state:
            return

        self.dispatcher.publish(
            StateChanged(
                timestamp=self.clock(),
                old_state=old_state,
                new_state=new_state,
                payload={
                    "old_state": old_state.value,
                    "new_state": new_state.value,
                },
            )
        )

    def _publish_session_started(self, session: SessionRecord) -> None:
        self.dispatcher.publish(
            SessionStarted(
                timestamp=self.clock(),
                payload={"session_id": session.id},
            )
        )

    def _publish_session_ended(self, session: SessionRecord) -> None:
        self.dispatcher.publish(
            SessionEnded(
                timestamp=self.clock(),
                payload={
                    "session_id": session.id,
                    "duration_seconds": session.duration_seconds,
                    "break_count": session.break_count,
                },
            )
        )

    def _publish_break_started(self, break_record: BreakRecord) -> None:
        self.dispatcher.publish(
            BreakStarted(
                timestamp=self.clock(),
                payload={
                    "break_id": break_record.id,
                    "session_id": break_record.session_id,
                },
            )
        )

    def _publish_break_ended(self, break_record: BreakRecord) -> None:
        self.dispatcher.publish(
            BreakEnded(
                timestamp=self.clock(),
                payload={
                    "break_id": break_record.id,
                    "session_id": break_record.session_id,
                    "duration_seconds": break_record.duration_seconds,
                },
            )
        )

    def _publish_statistics_updated(self) -> None:
        stats = self.stats_service.get_daily_stats()
        self.dispatcher.publish(
            StatisticsUpdated(
                timestamp=self.clock(),
                payload=stats.as_dict(),
            )
        )
