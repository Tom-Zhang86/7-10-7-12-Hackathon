from dataclasses import dataclass
import logging
from queue import Empty, Queue
from threading import Event as ThreadEvent
from threading import Thread
from typing import Any

from events.event_types import Event
from models.state import PresenceState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _StateCommand:
    state: PresenceState


class ApplicationController:
    """Connect application workers to the public API and runtime events.

    Runtime callbacks only enqueue work. Context capture and future UI/AI work
    therefore never block the system-layer runtime thread.
    """

    EVENT_NAMES = (
        "StateChanged",
        "StatisticsUpdated",
        "SessionStarted",
        "SessionEnded",
        "BreakStarted",
        "BreakEnded",
    )

    def __init__(self, api: Any, context_collector: Any) -> None:
        self.api = api
        self.context_collector = context_collector
        self._commands: Queue[_StateCommand | None] = Queue()
        self._updates: Queue[Event] = Queue()
        self._stop_signal = ThreadEvent()
        self._thread: Thread | None = None
        self._started = False

    def start(self, start_runtime: bool = True) -> None:
        """Subscribe to B's events and reconcile the current backend state."""

        if self._started:
            return

        self._started = True
        self._stop_signal.clear()
        for event_name in self.EVENT_NAMES:
            self.api.runtime.subscribe(event_name, self._on_runtime_event)

        self._thread = Thread(
            target=self._run,
            name="application-controller",
            daemon=True,
        )
        self._thread.start()

        if start_runtime:
            self.api.start()

        self._commands.put(_StateCommand(self.api.get_current_state()))

    def stop(self, stop_runtime: bool = False) -> None:
        """Stop application workers without ending the day by default."""

        if not self._started:
            return

        for event_name in self.EVENT_NAMES:
            self.api.runtime.unsubscribe(event_name, self._on_runtime_event)

        self._stop_signal.set()
        self._commands.put(None)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self.context_collector.stop()
        self._started = False

        if stop_runtime:
            self.api.stop()

    def get_updates(self) -> list[Event]:
        """Drain runtime notifications for a future UI main-thread loop."""

        updates: list[Event] = []
        while True:
            try:
                updates.append(self._updates.get_nowait())
            except Empty:
                return updates

    def wait_until_idle(self) -> None:
        """Wait until all queued application commands have been applied."""

        self._commands.join()

    def _on_runtime_event(self, event: Event) -> None:
        self._updates.put(event)
        if event.name != "StateChanged":
            return

        value = event.payload.get("new_state")
        try:
            state = PresenceState(value)
        except (TypeError, ValueError):
            logger.warning("Ignoring invalid StateChanged payload: %r", value)
            return
        self._commands.put(_StateCommand(state))

    def _run(self) -> None:
        while True:
            command = self._commands.get()
            try:
                if command is None:
                    return
                self._apply_state(command.state)
            except Exception:
                logger.exception("Application state reconciliation failed.")
            finally:
                self._commands.task_done()

    def _apply_state(self, state: PresenceState) -> None:
        if state != PresenceState.WORKING:
            self.context_collector.stop()
            return

        session = self.api.get_active_session()
        if session is None:
            logger.warning("Working state has no active session; capture skipped.")
            self.context_collector.stop()
            return
        self.context_collector.start(session.id)
