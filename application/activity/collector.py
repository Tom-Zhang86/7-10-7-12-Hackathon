from __future__ import annotations

from collections import deque
import logging
import math
import os
from threading import Event, Lock, Thread, current_thread
import time
from typing import Any, Callable
from urllib.parse import urlparse

from application.activity.analyzer import analyze_activity_window
from application.context.macos_provider import DesktopContext

logger = logging.getLogger(__name__)


class _NoopListener:
    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


class ActivityMetricsCollector:
    """Collect aggregate interaction metrics without retaining keys or positions."""

    def __init__(
        self,
        api: Any,
        *,
        window_seconds: float = 60.0,
        current_task: str | None = None,
        listener_factory: Callable[[Any, Any, Any, Any], tuple[Any, Any]]
        | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.api = api
        self.window_seconds = max(float(window_seconds), 5.0)
        self.current_task = (
            current_task
            if current_task is not None
            else os.getenv("AI_DESK_CURRENT_TASK", "")
        )
        self.listener_factory = listener_factory or self._noop_listeners
        self._poll_quartz = listener_factory is None
        self.monotonic = monotonic
        self._lock = Lock()
        self._stop_signal = Event()
        self._thread: Thread | None = None
        self._keyboard_listener: Any | None = None
        self._mouse_listener: Any | None = None
        self._session_id: int | None = None
        self._recent_interfaces: deque[str] = deque(maxlen=8)
        self._quartz_previous: dict[str, Any] | None = None
        self._reset_window(self.monotonic())

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, session_id: int) -> None:
        with self._lock:
            self._session_id = session_id
            if self.is_running:
                return
            self._reset_window(self.monotonic())
            self._stop_signal.clear()
        keyboard, mouse = self.listener_factory(
            self.record_keypress,
            self.record_mouse_move,
            self.record_mouse_click,
            self.record_scroll,
        )
        self._keyboard_listener = keyboard
        self._mouse_listener = mouse
        keyboard.start()
        mouse.start()
        self._thread = Thread(
            target=self._run,
            name="aggregate-activity-collector",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_signal.set()
        for listener in (self._keyboard_listener, self._mouse_listener):
            if listener is not None:
                listener.stop()
        thread = self._thread
        if thread and thread.is_alive() and thread is not current_thread():
            thread.join(timeout=2)
        self.flush()
        with self._lock:
            self._thread = None
            self._keyboard_listener = None
            self._mouse_listener = None
            self._session_id = None

    def observe_interface(self, context: DesktopContext) -> None:
        """Accept already-visible metadata from the normal context collector."""

        signature = " | ".join(
            value
            for value in (
                context.app,
                context.browser_title or context.window_title,
                context.browser_url,
            )
            if value
        )
        with self._lock:
            self._context = context
            if signature and (
                not self._recent_interfaces
                or self._recent_interfaces[-1] != signature
            ):
                if self._recent_interfaces:
                    self._interface_switch_count += 1
                self._recent_interfaces.append(signature)
                self._interface_started_at = self.monotonic()

    def record_keypress(self) -> None:
        """Record only a count and timestamp; no key value is accepted."""

        now = self.monotonic()
        with self._lock:
            self._keypress_count += 1
            if (
                self._last_keypress_at is None
                or now - self._last_keypress_at > 2.0
            ):
                self._typing_burst_count += 1
            if self._last_keypress_at is not None:
                self._longest_no_typing = max(
                    self._longest_no_typing,
                    now - self._last_keypress_at,
                )
            self._last_keypress_at = now

    def record_keypress_count(self, count: int) -> None:
        """Record an anonymous event-count delta from macOS Quartz."""

        if count <= 0:
            return
        now = self.monotonic()
        with self._lock:
            self._keypress_count += int(count)
            if (
                self._last_keypress_at is None
                or now - self._last_keypress_at > 2.0
            ):
                self._typing_burst_count += 1
            if self._last_keypress_at is not None:
                self._longest_no_typing = max(
                    self._longest_no_typing,
                    now - self._last_keypress_at,
                )
            self._last_keypress_at = now

    def record_mouse_move(self, x: float, y: float) -> None:
        """Use pointer coordinates transiently for distance, then discard them."""

        with self._lock:
            self._mouse_move_count += 1
            if self._last_mouse_position is not None:
                self._mouse_distance += math.dist(
                    self._last_mouse_position,
                    (float(x), float(y)),
                )
            self._last_mouse_position = (float(x), float(y))

    def record_mouse_click(self) -> None:
        with self._lock:
            self._mouse_click_count += 1

    def record_scroll(self) -> None:
        with self._lock:
            self._scrolling_count += 1

    def flush(self) -> dict[str, Any] | None:
        now = self.monotonic()
        with self._lock:
            session_id = self._session_id
            duration = max(now - self._window_started_at, 0.0)
            if session_id is None or duration < 1.0:
                return None
            context = self._context
            longest_pause = max(
                self._longest_no_typing,
                (
                    now
                    - (
                        self._last_keypress_at
                        if self._last_keypress_at is not None
                        else self._window_started_at
                    )
                ),
            )
            metrics = {
                "duration_seconds": round(duration, 2),
                "mouse_move_count": self._mouse_move_count,
                "mouse_distance": round(self._mouse_distance, 2),
                "mouse_click_count": self._mouse_click_count,
                "scrolling_count": self._scrolling_count,
                "keypress_count": self._keypress_count,
                "typing_burst_count": self._typing_burst_count,
                "average_typing_speed": round(
                    self._keypress_count / max(duration, 1.0) * 60,
                    2,
                ),
                "longest_no_typing_period": round(longest_pause, 2),
                "interface_switch_count": self._interface_switch_count,
                "recent_interfaces": list(self._recent_interfaces),
                "seconds_on_interface": round(
                    now - self._interface_started_at,
                    2,
                ),
            }
            self._reset_window(now, keep_context=True)

        browser_host = ""
        if context.browser_url:
            try:
                browser_host = urlparse(context.browser_url).hostname or ""
            except ValueError:
                pass
        result = analyze_activity_window(
            **metrics,
            current_task=self.current_task,
            application_name=context.app,
            window_title=context.window_title,
            website_domain=browser_host,
            page_title=context.browser_title,
            presence_sensor=True,
        )
        payload = {
            **metrics,
            "presence_sensor": True,
            "mouse_state": result.mouse_state.value,
            "keyboard_state": result.keyboard_state.value,
            "content_state": result.content_state.value,
            "switching_pattern": result.switching_pattern.value,
            "attention_state": result.attention_state.value,
        }
        self.api.record_context_event(
            session_id=session_id,
            source="attention_window",
            payload=payload,
        )
        return payload

    def _run(self) -> None:
        next_flush = self.monotonic() + self.window_seconds
        if self._poll_quartz:
            try:
                self._quartz_previous = self._quartz_snapshot()
            except Exception:
                logger.exception(
                    "Quartz aggregate input polling is unavailable; "
                    "attention windows will contain interface metrics only."
                )
                self._poll_quartz = False
        while not self._stop_signal.wait(0.5):
            if self._poll_quartz:
                try:
                    self._poll_quartz_once()
                except Exception:
                    logger.exception("Quartz aggregate input polling failed.")
                    self._poll_quartz = False
            if self.monotonic() >= next_flush:
                self.flush()
                next_flush = self.monotonic() + self.window_seconds

    def _reset_window(self, now: float, keep_context: bool = False) -> None:
        self._window_started_at = now
        self._mouse_move_count = 0
        self._mouse_distance = 0.0
        self._mouse_click_count = 0
        self._scrolling_count = 0
        self._keypress_count = 0
        self._typing_burst_count = 0
        self._last_keypress_at: float | None = None
        self._longest_no_typing = 0.0
        self._last_mouse_position: tuple[float, float] | None = None
        self._interface_switch_count = 0
        self._interface_started_at = now
        if not keep_context:
            self._context = DesktopContext("", "")
            self._recent_interfaces.clear()

    @staticmethod
    def _noop_listeners(*_callbacks):
        return _NoopListener(), _NoopListener()

    @staticmethod
    def _quartz_snapshot() -> dict[str, Any]:
        import Quartz

        state = Quartz.kCGEventSourceStateCombinedSessionState
        event_types = {
            "keys": (Quartz.kCGEventKeyDown,),
            "moves": (
                Quartz.kCGEventMouseMoved,
                Quartz.kCGEventLeftMouseDragged,
                Quartz.kCGEventRightMouseDragged,
                Quartz.kCGEventOtherMouseDragged,
            ),
            "clicks": (
                Quartz.kCGEventLeftMouseDown,
                Quartz.kCGEventRightMouseDown,
                Quartz.kCGEventOtherMouseDown,
            ),
            "scrolls": (Quartz.kCGEventScrollWheel,),
        }
        counters = {
            name: sum(
                int(Quartz.CGEventSourceCounterForEventType(state, event_type))
                for event_type in types
            )
            for name, types in event_types.items()
        }
        event = Quartz.CGEventCreate(None)
        point = Quartz.CGEventGetLocation(event)
        counters["position"] = (float(point.x), float(point.y))
        return counters

    @staticmethod
    def _counter_delta(current: int, previous: int) -> int:
        if current >= previous:
            return current - previous
        return (2**32 - previous) + current

    def _poll_quartz_once(self) -> None:
        current = self._quartz_snapshot()
        previous = self._quartz_previous
        self._quartz_previous = current
        if previous is None:
            return
        key_delta = self._counter_delta(current["keys"], previous["keys"])
        move_delta = self._counter_delta(current["moves"], previous["moves"])
        click_delta = self._counter_delta(
            current["clicks"], previous["clicks"]
        )
        scroll_delta = self._counter_delta(
            current["scrolls"], previous["scrolls"]
        )
        self.record_keypress_count(key_delta)
        with self._lock:
            self._mouse_move_count += move_delta
            self._mouse_click_count += click_delta
            self._scrolling_count += scroll_delta
            if move_delta:
                self._mouse_distance += math.dist(
                    previous["position"],
                    current["position"],
                )
