from collections.abc import Callable
from datetime import datetime
from typing import Optional

from database.repository import SessionRepository
from models.session_record import BreakRecord, SessionRecord
from models.state import PresenceState
from utils.time_utils import utc_now


class SessionManager:
    """State machine for desk presence sessions.

    The manager receives simple presence events from the hardware layer:
    present=True means a person is at the desk, present=False means the person
    is away. It turns those events into durable session and break records.
    """

    def __init__(
        self,
        repository: SessionRepository,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.repository = repository
        self.clock = clock
        self.state = PresenceState.IDLE
        self.current_session_id: Optional[int] = None

        self._restore_active_state()

    def handle_presence(self, present: bool) -> PresenceState:
        """Apply one hardware presence event and return the current state."""

        if self.state == PresenceState.FINISHED:
            return self.state

        if present and self.state == PresenceState.IDLE:
            self.start_work()
        elif not present and self.state == PresenceState.WORKING:
            self.start_break()
        elif present and self.state == PresenceState.BREAK:
            self.end_break()

        return self.state

    def start_work(self) -> SessionRecord:
        """Start a new work session from Idle."""

        if self.state != PresenceState.IDLE:
            raise RuntimeError(f"Cannot start work from {self.state.value}.")

        started_at = self.clock()
        session = self.repository.create_session(started_at)
        self.current_session_id = session.id
        self.state = PresenceState.WORKING
        return session

    def end_work(self) -> SessionRecord:
        """Finish the active session and move to Finished."""

        if self.state not in {PresenceState.WORKING, PresenceState.BREAK}:
            raise RuntimeError(f"Cannot end work from {self.state.value}.")

        session_id = self._require_session_id()
        ended_at = self.clock()

        open_break = self.repository.get_open_break(session_id)
        if open_break:
            self.repository.finish_break(open_break.id, ended_at)

        duration = self._calculate_net_work_seconds(session_id, ended_at)
        break_count = self.repository.count_breaks_for_session(session_id)
        session = self.repository.finish_session(
            session_id=session_id,
            end_time=ended_at,
            duration_seconds=duration,
            break_count=break_count,
        )

        self.current_session_id = None
        self.state = PresenceState.FINISHED
        return session

    def start_break(self) -> BreakRecord:
        """Start a break while the user is away from the desk."""

        if self.state != PresenceState.WORKING:
            raise RuntimeError(f"Cannot start break from {self.state.value}.")

        session_id = self._require_session_id()
        started_at = self.clock()
        break_record = self.repository.create_break(session_id, started_at)

        duration = self._calculate_net_work_seconds(session_id, started_at)
        break_count = self.repository.count_breaks_for_session(session_id)
        self.repository.update_session_progress(
            session_id=session_id,
            duration_seconds=duration,
            break_count=break_count,
            updated_at=started_at,
        )

        self.state = PresenceState.BREAK
        return break_record

    def end_break(self) -> BreakRecord:
        """End the current break and resume work."""

        if self.state != PresenceState.BREAK:
            raise RuntimeError(f"Cannot end break from {self.state.value}.")

        session_id = self._require_session_id()
        open_break = self.repository.get_open_break(session_id)
        if open_break is None:
            raise RuntimeError("No open break exists for the active session.")

        ended_break = self.repository.finish_break(open_break.id, self.clock())
        self.state = PresenceState.WORKING
        return ended_break

    def finish_day(self) -> Optional[SessionRecord]:
        """Finish the current day.

        Returns the closed session when one is active. If the system is still
        idle, it simply marks the lifecycle as Finished.
        """

        if self.state == PresenceState.IDLE:
            self.state = PresenceState.FINISHED
            return None
        if self.state == PresenceState.FINISHED:
            return None
        return self.end_work()

    def get_current_session(self) -> Optional[SessionRecord]:
        """Return the active session, if there is one."""

        if self.current_session_id is None:
            return None
        return self.repository.get_session(self.current_session_id)

    def _restore_active_state(self) -> None:
        """Recover an unfinished session after a process restart."""

        active_session = self.repository.get_active_session()
        if active_session is None:
            return

        self.current_session_id = active_session.id
        open_break = self.repository.get_open_break(active_session.id)
        self.state = PresenceState.BREAK if open_break else PresenceState.WORKING

    def _require_session_id(self) -> int:
        if self.current_session_id is None:
            raise RuntimeError("No active session.")
        return self.current_session_id

    def _calculate_net_work_seconds(
        self,
        session_id: int,
        end_time: datetime,
    ) -> int:
        session = self.repository.get_session(session_id)
        total_seconds = int((end_time - session.start_time).total_seconds())
        breaks = self.repository.list_breaks_for_session(session_id)

        break_seconds = 0
        for break_record in breaks:
            break_end = break_record.end_time or end_time
            break_seconds += int(
                (break_end - break_record.start_time).total_seconds()
            )

        return max(total_seconds - break_seconds, 0)
