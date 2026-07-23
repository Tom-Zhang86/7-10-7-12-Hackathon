from collections.abc import Callable, Iterable
from datetime import datetime
import logging
from threading import Event as ThreadEvent
from threading import Lock, Thread, current_thread
from typing import Any

from application.activity.heartbeat import HeartbeatReducer
from application.activity.models import ActivityObservation, ActivitySpan
from application.activity.privacy import sanitize_activity_data
from utils.time_utils import utc_now


logger = logging.getLogger(__name__)


class ActivityCoordinator:
    """Poll internal watchers and persist compact heartbeat spans.

    The start(session_id)/stop() surface intentionally matches ContextCollector
    so ApplicationController and the system-layer API do not need to change.
    """

    def __init__(
        self,
        api: Any,
        sources: Iterable[Any],
        store: Any,
        poll_seconds: float = 5.0,
        pulsetime_seconds: float = 20.0,
        compatibility_heartbeat_seconds: float = 60.0,
        privacy_policy: Any | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.api = api
        self.sources = tuple(sources)
        self.store = store
        self.poll_seconds = poll_seconds
        self.compatibility_heartbeat_seconds = compatibility_heartbeat_seconds
        self.privacy_policy = privacy_policy
        self.clock = clock
        self.reducer = HeartbeatReducer(pulsetime_seconds)

        self._stop_signal = ThreadEvent()
        self._lock = Lock()
        self._thread: Thread | None = None
        self._session_id: int | None = None
        self._last_compatibility_hash: dict[str, str] = {}
        self._last_compatibility_at: dict[str, datetime] = {}

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, session_id: int) -> None:
        with self._lock:
            self._session_id = session_id
            if self.is_running:
                return
            self._stop_signal.clear()
            for source in self.sources:
                source.start()
            self._thread = Thread(
                target=self._run,
                name="activity-coordinator",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            self._stop_signal.set()

        if thread and thread.is_alive() and thread is not current_thread():
            thread.join(timeout=max(self.poll_seconds + 1, 2))

        for source in self.sources:
            try:
                source.stop()
            except Exception:
                logger.exception("Activity source failed to stop.")

        self._persist(self.reducer.flush(self.clock()))
        with self._lock:
            self._thread = None
            self._session_id = None
        self._last_compatibility_hash.clear()
        self._last_compatibility_at.clear()

    def capture_once(self, session_id: int) -> int:
        """Capture all sources once and return the observation count."""

        captured = 0
        for source in self.sources:
            try:
                observation = source.capture()
            except Exception:
                logger.exception("Activity source capture failed.")
                continue
            if observation is None:
                continue
            self._process_observation(observation, session_id)
            captured += 1
        return captured

    def _run(self) -> None:
        while not self._stop_signal.is_set():
            with self._lock:
                session_id = self._session_id

            if session_id is not None:
                try:
                    self.capture_once(session_id)
                except Exception:
                    logger.exception("Activity capture failed.")

            self._stop_signal.wait(self.poll_seconds)

    def _process_observation(
        self,
        observation: ActivityObservation,
        session_id: int,
    ) -> None:
        sanitized = ActivityObservation(
            timestamp=observation.timestamp,
            bucket_id=observation.bucket_id,
            event_type=observation.event_type,
            source=observation.source,
            data=sanitize_activity_data(observation.data),
        )
        if self.privacy_policy and not self.privacy_policy.allows(sanitized.data):
            return
        self._persist(self.reducer.ingest(sanitized))
        if sanitized.event_type == "currentwindow":
            self._record_compatibility_event(sanitized, session_id)

    def _persist(self, spans: Iterable[ActivitySpan]) -> None:
        for span in spans:
            self.store.upsert_span(span)

    def _record_compatibility_event(
        self,
        observation: ActivityObservation,
        session_id: int,
    ) -> None:
        previous_hash = self._last_compatibility_hash.get(observation.bucket_id)
        previous_at = self._last_compatibility_at.get(observation.bucket_id)
        heartbeat_due = (
            previous_at is None
            or (
                observation.timestamp - previous_at
            ).total_seconds() >= self.compatibility_heartbeat_seconds
        )
        if previous_hash == observation.content_hash and not heartbeat_due:
            return

        self.api.record_context_event(
            session_id=session_id,
            source=observation.source,
            payload=dict(observation.data),
        )
        self._last_compatibility_hash[observation.bucket_id] = (
            observation.content_hash
        )
        self._last_compatibility_at[observation.bucket_id] = observation.timestamp
