from collections.abc import Callable
from datetime import date, datetime

from database.repository import SessionRepository
from models.session_record import BreakRecord, SessionRecord
from models.stats import DailyStats
from utils.time_utils import utc_now


class StatsService:
    """Calculates daily work statistics from stored session data."""

    def __init__(
        self,
        repository: SessionRepository,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.repository = repository
        self.clock = clock

    def get_daily_stats(self, target_date: date | None = None) -> DailyStats:
        """Return total work time, sessions, breaks, and longest focus span."""

        target_date = target_date or self.clock().date()
        now = self.clock()
        sessions = self.repository.list_sessions_for_day(target_date)

        total_work_seconds = sum(
            self._net_work_seconds(session, now) for session in sessions
        )
        break_count = sum(
            self.repository.count_breaks_for_session(session.id)
            for session in sessions
        )
        longest_focus_seconds = max(
            (
                self._longest_focus_seconds(
                    session,
                    self.repository.list_breaks_for_session(session.id),
                    now,
                )
                for session in sessions
            ),
            default=0,
        )

        return DailyStats(
            total_work_seconds=total_work_seconds,
            session_count=len(sessions),
            break_count=break_count,
            longest_focus_seconds=longest_focus_seconds,
        )

    def _net_work_seconds(
        self,
        session: SessionRecord,
        now: datetime,
    ) -> int:
        if session.end_time is not None:
            return session.duration_seconds

        session_end = session.end_time or now
        total_seconds = int((session_end - session.start_time).total_seconds())
        break_seconds = 0
        for break_record in self.repository.list_breaks_for_session(session.id):
            break_end = break_record.end_time or now
            break_seconds += int(
                (break_end - break_record.start_time).total_seconds()
            )

        return max(total_seconds - break_seconds, 0)

    @staticmethod
    def _longest_focus_seconds(
        session: SessionRecord,
        breaks: list[BreakRecord],
        now: datetime,
    ) -> int:
        session_end = session.end_time or now
        cursor = session.start_time
        longest = 0

        for break_record in breaks:
            longest = max(
                longest,
                int((break_record.start_time - cursor).total_seconds()),
            )
            cursor = break_record.end_time or session_end

        longest = max(longest, int((session_end - cursor).total_seconds()))
        return max(longest, 0)
