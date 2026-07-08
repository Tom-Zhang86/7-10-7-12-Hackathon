from datetime import date
from pathlib import Path
from typing import Any, Optional

from database.connection import Database
from database.repository import SessionRepository
from events.event_types import Event, PresenceDetected, PresenceLost
from listeners.event_log_listener import EventLogListener
from models.session_record import BreakRecord, SessionRecord
from models.state import PresenceState
from runtime.runtime import Runtime
from session.manager import SessionManager
from services.stats_service import StatsService
from utils.time_utils import utc_now


class AIDeskPresenceAPI:
    """Unified system-layer API for future AI modules.

    AI, UI, serial communication, and context-capture modules should call this
    class instead of reaching into the session manager or database directly.
    """

    def __init__(
        self,
        db_path: str | Path = "ai_desk_presence.db",
        log_dir: str | Path = "logs",
    ) -> None:
        self.database = Database(db_path)
        self.database.initialize()
        self.repository = SessionRepository(self.database)
        self.session_manager = SessionManager(self.repository)
        self.stats_service = StatsService(self.repository)
        self.runtime = Runtime(
            session_manager=self.session_manager,
            stats_service=self.stats_service,
        )
        self.event_logger = EventLogListener(log_dir)
        self.runtime.subscribe("*", self.event_logger)

    def start(self, block: bool = False) -> None:
        """Start the long-running event runtime."""

        self.runtime.start(block=block)

    def stop(self) -> None:
        """Gracefully stop the long-running event runtime."""

        self.runtime.stop()

    def close(self) -> None:
        """Release runtime and database resources."""

        self.stop()
        self.database.close()

    def post_event(self, event: Event) -> None:
        """Post a runtime event from hardware, AI, UI, or tests."""

        self.runtime.post_event(event)

    def wait_until_idle(self) -> None:
        """Wait until all queued runtime events have been processed."""

        self.runtime.wait_until_idle()

    def ingest_presence(self, present: bool) -> PresenceState:
        """Feed one LD2410 presence value into the state machine."""

        event = PresenceDetected() if present else PresenceLost()
        if self.runtime.is_running:
            self.post_event(event)
            self.wait_until_idle()
            return self.get_current_state()

        return self.session_manager.handle_presence(present)

    def start_work(self) -> SessionRecord:
        """Manually start a work session for tests or future integrations."""

        return self.session_manager.start_work()

    def end_work(self) -> SessionRecord:
        """Manually end the active work session."""

        return self.session_manager.end_work()

    def start_break(self) -> BreakRecord:
        """Manually start a break."""

        return self.session_manager.start_break()

    def end_break(self) -> BreakRecord:
        """Manually end the current break."""

        return self.session_manager.end_break()

    def finish_day(self) -> Optional[SessionRecord]:
        """Close the active lifecycle at the end of the day."""

        return self.session_manager.finish_day()

    def get_current_state(self) -> PresenceState:
        """Return the current state machine state."""

        return self.session_manager.state

    def get_state(self) -> PresenceState:
        """Return the current state machine state."""

        return self.get_current_state()

    def get_current_session(self) -> Optional[SessionRecord]:
        """Return the active session record, if any."""

        return self.session_manager.get_current_session()

    def get_active_session(self) -> Optional[SessionRecord]:
        """Return the active session record, if any."""

        return self.get_current_session()

    def get_today_stats(self) -> dict[str, Any]:
        """Return today's stats as a plain dictionary for AI consumption."""

        return self.stats_service.get_daily_stats().as_dict()

    def get_stats_for_day(self, target_date: date) -> dict[str, Any]:
        """Return stats for a specific UTC calendar day."""

        return self.stats_service.get_daily_stats(target_date).as_dict()

    def record_context_event(
        self,
        session_id: Optional[int],
        source: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Record a desktop or OS context event as JSON."""

        if not isinstance(source, str) or not source:
            raise ValueError("source must be a non-empty string.")
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dictionary.")

        record = self.repository.create_context_event(
            session_id=session_id,
            source=source,
            payload=payload,
            captured_at=utc_now(),
        )
        return record.as_dict()

    def get_context_events_for_day(
        self,
        date: date | None = None,
    ) -> list[dict[str, Any]]:
        """Return context events for one UTC calendar day."""

        target_date = date or utc_now().date()
        return [
            record.as_dict()
            for record in self.repository.list_context_events_for_day(target_date)
        ]

    def get_today_timeline(self) -> list[dict[str, Any]]:
        """Return today's sessions, breaks, and context events in time order."""

        today = utc_now().date()
        items: list[dict[str, Any]] = []

        for session in self.repository.list_sessions_for_day(today):
            items.append(
                {
                    "type": "session",
                    "timestamp": session.start_time,
                    "start_time": session.start_time,
                    "end_time": session.end_time,
                    "session_id": session.id,
                    "duration_seconds": session.duration_seconds,
                    "break_count": session.break_count,
                }
            )

        for break_record in self.repository.list_breaks_for_day(today):
            items.append(
                {
                    "type": "break",
                    "timestamp": break_record.start_time,
                    "start_time": break_record.start_time,
                    "end_time": break_record.end_time,
                    "session_id": break_record.session_id,
                    "break_id": break_record.id,
                    "duration_seconds": break_record.duration_seconds,
                }
            )

        for context_event in self.repository.list_context_events_for_day(today):
            items.append(
                {
                    "type": "context_event",
                    "timestamp": context_event.timestamp,
                    "session_id": context_event.session_id,
                    "source": context_event.source,
                    "payload": context_event.payload,
                }
            )

        return sorted(items, key=lambda item: item["timestamp"])
