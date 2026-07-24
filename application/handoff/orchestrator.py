from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
from threading import Lock, Timer
from typing import Any

from application.handoff.models import HandoffStatus
from models.state import PresenceState


logger = logging.getLogger(__name__)


class HandoffOrchestrator:
    """Move one armed task to an A2A agent when the user leaves the desk."""

    def __init__(
        self,
        api: Any,
        store: Any,
        client: Any,
        actions: Any,
        *,
        grace_seconds: float = 3.0,
    ) -> None:
        self.api = api
        self.store = store
        self.client = client
        self.actions = actions
        self.grace_seconds = max(float(grace_seconds), 0.0)
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="handoff-orchestrator",
        )
        self._timer: Timer | None = None
        self._lock = Lock()
        self._current_state = api.get_current_state()
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self.api.runtime.subscribe("StateChanged", self._on_state_changed)
        self._current_state = self.api.get_current_state()
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        self.api.runtime.unsubscribe("StateChanged", self._on_state_changed)
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._started = False

    def _on_state_changed(self, event: Any) -> None:
        try:
            state = PresenceState(event.payload.get("new_state"))
        except (TypeError, ValueError):
            logger.warning("Ignoring invalid handoff state event: %r", event.payload)
            return
        with self._lock:
            self._current_state = state
        if state is PresenceState.BREAK:
            self._schedule_delegation()
        elif state is PresenceState.WORKING:
            self._cancel_pending_delegation()
            self._executor.submit(self._handle_return)

    def _schedule_delegation(self) -> None:
        self._cancel_pending_delegation()
        timer = Timer(self.grace_seconds, self._grace_elapsed)
        timer.daemon = True
        with self._lock:
            self._timer = timer
        timer.start()

    def _cancel_pending_delegation(self) -> None:
        with self._lock:
            timer = self._timer
            self._timer = None
        if timer is not None:
            timer.cancel()

    def _grace_elapsed(self) -> None:
        with self._lock:
            self._timer = None
            state = self._current_state
        if state is PresenceState.BREAK:
            self._executor.submit(self._delegate_next)

    def _delegate_next(self) -> None:
        record = self.store.claim_next_armed()
        if record is None:
            return
        handoff_id = record.capsule.handoff_id
        try:
            self.store.mark_running(handoff_id)
            self.actions.notify(
                "AI Desk handoff",
                "Task delegated to the Research Handoff Agent.",
            )
            result = self.client.send_task(record.capsule)
            completed = self.store.complete(
                handoff_id,
                a2a_task_id=result.task_id,
                context_id=result.context_id,
                artifact=result.artifact,
            )
        except Exception as exc:
            logger.exception("Research handoff failed.")
            try:
                completed = self.store.mark_failed(
                    handoff_id,
                    f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                logger.exception("Could not persist failed handoff state.")
                return

        with self._lock:
            user_is_present = self._current_state is PresenceState.WORKING
        if user_is_present and completed.status in {
            HandoffStatus.READY,
            HandoffStatus.INPUT_REQUIRED,
            HandoffStatus.FAILED,
        }:
            self._deliver_next()

    def _handle_return(self) -> None:
        if self._deliver_next():
            return
        if any(
            record.status in {HandoffStatus.DELEGATING, HandoffStatus.RUNNING}
            for record in self.store.list_all()
        ):
            self.actions.notify(
                "AI Desk handoff",
                "The Research Handoff Agent is still working.",
            )

    def _deliver_next(self) -> bool:
        record = self.store.next_deliverable()
        if record is None:
            return False
        returned = self.store.mark_returned(record.capsule.handoff_id)
        self.actions.deliver(returned)
        return True
