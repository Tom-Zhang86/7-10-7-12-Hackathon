from __future__ import annotations

from collections import deque
import logging
import math
import os
import re
import statistics
from threading import Event, Lock, Thread, current_thread
import time
from typing import Any, Callable
from urllib.parse import urlparse

from application.activity.analyzer import analyze_activity_window
from application.context.macos_provider import DesktopContext

logger = logging.getLogger(__name__)
_MOUSE_ACTIVE_TAIL_SECONDS = 2.0
_DIRECTION_BUCKET_COUNT = 8
_USER_INITIATED_SWITCH_SECONDS = 6.0
_AUTOMATIC_SWITCH_SECONDS = 15.0


def _interface_tokens(signature: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[\w+#.-]{2,}", signature.casefold())
        if not token.isdigit() and token not in {"http", "https", "www"}
    }


def _semantic_similarity(first: str, second: str) -> float:
    left = _interface_tokens(first)
    right = _interface_tokens(second)
    if not left and not right:
        return 1.0
    return len(left & right) / len(left | right) if left or right else 0.0


class _NoopListener:
    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


class _QuartzEventListener:
    """One native listen-only event tap shared by keyboard and mouse inputs."""

    def __init__(
        self,
        key_press: Any,
        mouse_move: Any,
        mouse_click: Any,
        mouse_scroll: Any,
    ) -> None:
        self.key_press = key_press
        self.mouse_move = mouse_move
        self.mouse_click = mouse_click
        self.mouse_scroll = mouse_scroll
        self._thread: Thread | None = None
        self._run_loop: Any = None
        self._tap: Any = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = Thread(
            target=self._run,
            name="quartz-input-listener",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._run_loop is not None:
            import Quartz

            Quartz.CFRunLoopStop(self._run_loop)
        thread = self._thread
        if thread and thread.is_alive() and thread is not current_thread():
            thread.join(timeout=1)

    def _run(self) -> None:
        import Quartz

        observed_types = (
            Quartz.kCGEventKeyDown,
            Quartz.kCGEventMouseMoved,
            Quartz.kCGEventLeftMouseDragged,
            Quartz.kCGEventRightMouseDragged,
            Quartz.kCGEventOtherMouseDragged,
            Quartz.kCGEventLeftMouseDown,
            Quartz.kCGEventRightMouseDown,
            Quartz.kCGEventOtherMouseDown,
            Quartz.kCGEventScrollWheel,
        )
        mask = sum(1 << event_type for event_type in observed_types)
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            self._handle_event,
            None,
        )
        if self._tap is None:
            logger.warning(
                "Input event tap is unavailable; grant Input Monitoring "
                "permission and restart AI Desk."
            )
            return
        source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        self._run_loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(
            self._run_loop,
            source,
            Quartz.kCFRunLoopCommonModes,
        )
        Quartz.CGEventTapEnable(self._tap, True)
        Quartz.CFRunLoopRun()

    def _handle_event(
        self,
        _proxy: Any,
        event_type: int,
        event: Any,
        _refcon: Any,
    ) -> Any:
        import Quartz

        if event_type in (
            Quartz.kCGEventTapDisabledByTimeout,
            Quartz.kCGEventTapDisabledByUserInput,
        ):
            if self._tap is not None:
                Quartz.CGEventTapEnable(self._tap, True)
            return event
        if event_type == Quartz.kCGEventKeyDown:
            keycode = int(
                Quartz.CGEventGetIntegerValueField(
                    event,
                    Quartz.kCGKeyboardEventKeycode,
                )
            )
            flags = int(Quartz.CGEventGetFlags(event))
            self.key_press(("quartz", keycode, flags))
        elif event_type in (
            Quartz.kCGEventMouseMoved,
            Quartz.kCGEventLeftMouseDragged,
            Quartz.kCGEventRightMouseDragged,
            Quartz.kCGEventOtherMouseDragged,
        ):
            point = Quartz.CGEventGetLocation(event)
            self.mouse_move(float(point.x), float(point.y))
        elif event_type in (
            Quartz.kCGEventLeftMouseDown,
            Quartz.kCGEventRightMouseDown,
            Quartz.kCGEventOtherMouseDown,
        ):
            self.mouse_click()
        elif event_type == Quartz.kCGEventScrollWheel:
            self.mouse_scroll()
        return event


class ActivityMetricsCollector:
    """Collect aggregate interaction metrics without retaining keys or positions."""

    def __init__(
        self,
        api: Any,
        *,
        window_seconds: float = 60.0,
        current_task: str | None = None,
        listener_factory: Callable[..., tuple[Any, Any]]
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
        self.listener_factory = listener_factory or self._quartz_event_listeners
        self._poll_quartz = False
        self._keyboard_detail_available = listener_factory is None
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
            self.record_key_release,
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
        now = self.monotonic()
        with self._lock:
            self._context = context
            if signature and (
                not self._recent_interfaces
                or self._recent_interfaces[-1] != signature
            ):
                if self._recent_interfaces:
                    self._interface_switch_count += 1
                    previous = self._recent_interfaces[-1]
                    self._interface_dwell_seconds.append(
                        max(now - self._interface_started_at, 0.0)
                    )
                    self._interface_similarities.append(
                        _semantic_similarity(previous, signature)
                    )
                    if signature in list(self._recent_interfaces)[:-1]:
                        self._interface_return_count += 1
                        self._interface_return_examples.append(
                            f"{previous} → {signature}"
                        )
                    since_action = (
                        now - self._last_action_input_at
                        if self._last_action_input_at is not None
                        else None
                    )
                    if (
                        since_action is not None
                        and since_action <= _USER_INITIATED_SWITCH_SECONDS
                    ):
                        origin = "LIKELY_USER_INITIATED"
                    elif (
                        since_action is None
                        or since_action >= _AUTOMATIC_SWITCH_SECONDS
                    ):
                        origin = "LIKELY_AUTOMATIC"
                    else:
                        origin = "UNKNOWN"
                    self._interface_origin_counts[origin] += 1
                self._recent_interfaces.append(signature)
                self._interface_started_at = now

    def record_keypress(self, key: Any = None) -> None:
        """Process a key transiently and retain only timing/category aggregates."""

        now = self.monotonic()
        with self._lock:
            shortcut_from_event = False
            if (
                isinstance(key, tuple)
                and len(key) == 3
                and key[0] == "quartz"
            ):
                keycode = int(key[1])
                flags = int(key[2])
                token = f"keycode:{keycode}"
                category = (
                    "correction"
                    if keycode in {51, 117}
                    else "ordinary"
                )
                # Command, Shift, Control, and Option flag masks.
                shortcut_from_event = bool(flags & 0x001E0000)
            else:
                token, category = self._key_token(key)
            if category == "modifier":
                self._pressed_modifiers.add(token)
                return
            self._keypress_count += 1
            self._last_action_input_at = now
            if category == "correction":
                self._correction_count += 1
            if self._pressed_modifiers or shortcut_from_event:
                self._shortcut_count += 1
            if token and token == self._last_key_token:
                self._repeated_key_count += 1
            self._last_key_token = token
            if (
                self._last_keypress_at is None
                or now - self._last_keypress_at > 2.0
            ):
                self._typing_burst_count += 1
                self._burst_lengths.append(0)
                self._burst_started_at = now
            else:
                interval = max(now - self._last_keypress_at, 0.0)
                self._inter_key_intervals.append(interval)
                self._pause_buckets[self._pause_bucket(interval)] += 1
            self._burst_lengths[-1] += 1
            if self._last_keypress_at is not None:
                self._longest_no_typing = max(
                    self._longest_no_typing,
                    now - self._last_keypress_at,
                )
            self._last_keypress_at = now

    def record_key_release(self, key: Any) -> None:
        token, category = self._key_token(key)
        if category == "modifier":
            with self._lock:
                self._pressed_modifiers.discard(token)

    def record_keypress_count(self, count: int) -> None:
        """Record an anonymous event-count delta from macOS Quartz."""

        if count <= 0:
            return
        now = self.monotonic()
        with self._lock:
            self._keypress_count += int(count)
            self._keyboard_detail_available = False
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

        now = self.monotonic()
        position = (float(x), float(y))
        with self._lock:
            self._mouse_move_count += 1
            self._record_mouse_activity_locked(now)
            if self._last_mouse_position is not None:
                dx = position[0] - self._last_mouse_position[0]
                dy = position[1] - self._last_mouse_position[1]
                self._mouse_distance += math.hypot(dx, dy)
                self._record_direction_locked(dx, dy)
            elif self._mouse_start_position is None:
                self._mouse_start_position = position
            self._last_mouse_position = position

    def record_mouse_click(self) -> None:
        now = self.monotonic()
        with self._lock:
            self._mouse_click_count += 1
            self._last_action_input_at = now
            self._record_mouse_activity_locked(now)

    def record_scroll(self) -> None:
        now = self.monotonic()
        with self._lock:
            self._scrolling_count += 1
            self._last_action_input_at = now
            self._record_mouse_activity_locked(now)

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
            mouse_metrics = self._mouse_metrics_locked(now, duration)
            keyboard_metrics = self._keyboard_metrics_locked(duration)
            interface_metrics = self._interface_metrics_locked(now, duration)
            metrics = {
                "duration_seconds": round(duration, 2),
                "mouse_move_count": self._mouse_move_count,
                "mouse_distance": round(self._mouse_distance, 2),
                "mouse_click_count": self._mouse_click_count,
                "scrolling_count": self._scrolling_count,
                **mouse_metrics,
                "keypress_count": self._keypress_count,
                "typing_burst_count": self._typing_burst_count,
                **keyboard_metrics,
                "average_typing_speed": round(
                    self._keypress_count / max(duration, 1.0) * 60,
                    2,
                ),
                "longest_no_typing_period": round(longest_pause, 2),
                "interface_switch_count": self._interface_switch_count,
                "recent_interfaces": list(self._recent_interfaces),
                **interface_metrics,
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
            **{
                key: metrics[key]
                for key in (
                    "duration_seconds",
                    "mouse_move_count",
                    "mouse_distance",
                    "mouse_click_count",
                    "scrolling_count",
                    "keypress_count",
                    "typing_burst_count",
                    "average_typing_speed",
                    "longest_no_typing_period",
                    "seconds_on_interface",
                    "interface_switch_count",
                    "recent_interfaces",
                )
            },
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
        self._last_key_token: str | None = None
        self._pressed_modifiers: set[str] = set()
        self._correction_count = 0
        self._shortcut_count = 0
        self._repeated_key_count = 0
        self._burst_lengths: list[int] = []
        self._burst_started_at: float | None = None
        self._inter_key_intervals: list[float] = []
        self._pause_buckets = {
            "under_1s": 0,
            "1_to_3s": 0,
            "3_to_10s": 0,
            "10s_or_more": 0,
        }
        self._longest_no_typing = 0.0
        self._last_mouse_position: tuple[float, float] | None = None
        self._mouse_start_position: tuple[float, float] | None = None
        self._last_mouse_activity_at: float | None = None
        self._first_mouse_activity_at: float | None = None
        self._mouse_active_seconds = 0.0
        self._mouse_idle_gaps: list[float] = []
        self._direction_counts = [0] * _DIRECTION_BUCKET_COUNT
        self._interface_switch_count = 0
        self._interface_started_at = now
        self._interface_dwell_seconds: list[float] = []
        self._interface_similarities: list[float] = []
        self._interface_return_count = 0
        self._interface_return_examples: deque[str] = deque(maxlen=3)
        self._interface_origin_counts = {
            "LIKELY_USER_INITIATED": 0,
            "LIKELY_AUTOMATIC": 0,
            "UNKNOWN": 0,
        }
        if not keep_context or not hasattr(self, "_last_action_input_at"):
            self._last_action_input_at: float | None = None
        if not keep_context:
            self._context = DesktopContext("", "")
            self._recent_interfaces.clear()

    @staticmethod
    def _noop_listeners(*_callbacks):
        return _NoopListener(), _NoopListener()

    @staticmethod
    def _quartz_event_listeners(
        key_press: Any,
        _key_release: Any,
        mouse_move: Any,
        mouse_click: Any,
        mouse_scroll: Any,
    ):
        listener = _QuartzEventListener(
            key_press,
            mouse_move,
            mouse_click,
            mouse_scroll,
        )
        return listener, listener

    @staticmethod
    def _key_token(key: Any) -> tuple[str, str]:
        """Return an ephemeral identity and a non-content key category."""

        if key is None:
            return "", "ordinary"
        name = str(getattr(key, "name", "") or "").casefold()
        representation = str(key).casefold()
        token = name or representation
        if any(
            marker in token
            for marker in ("cmd", "ctrl", "alt", "option", "shift")
        ):
            return token, "modifier"
        if "backspace" in token or token.endswith("delete"):
            return token, "correction"
        return token, "ordinary"

    @staticmethod
    def _pause_bucket(seconds: float) -> str:
        if seconds < 1:
            return "under_1s"
        if seconds < 3:
            return "1_to_3s"
        if seconds < 10:
            return "3_to_10s"
        return "10s_or_more"

    def _keyboard_metrics_locked(
        self,
        duration: float,
    ) -> dict[str, Any]:
        intervals = self._inter_key_intervals
        bursts = self._burst_lengths
        keypresses = self._keypress_count
        return {
            "keyboard_detail_available": self._keyboard_detail_available,
            "keystrokes_per_minute": round(
                keypresses / max(duration / 60.0, 1 / 60.0),
                2,
            ),
            "average_typing_burst_length": round(
                sum(bursts) / len(bursts) if bursts else 0.0,
                2,
            ),
            "longest_typing_burst_length": max(bursts, default=0),
            "average_inter_key_interval_seconds": round(
                sum(intervals) / len(intervals) if intervals else 0.0,
                3,
            ),
            "median_inter_key_interval_seconds": round(
                statistics.median(intervals) if intervals else 0.0,
                3,
            ),
            "typing_pause_distribution": dict(self._pause_buckets),
            "correction_count": self._correction_count,
            "correction_rate": round(
                self._correction_count / keypresses if keypresses else 0.0,
                4,
            ),
            "shortcut_count": self._shortcut_count,
            "shortcut_rate": round(
                self._shortcut_count / keypresses if keypresses else 0.0,
                4,
            ),
            "repetition_count": self._repeated_key_count,
            "repetition_rate": round(
                self._repeated_key_count / keypresses if keypresses else 0.0,
                4,
            ),
        }

    def _interface_metrics_locked(
        self,
        now: float,
        duration: float,
    ) -> dict[str, Any]:
        dwell_times = list(self._interface_dwell_seconds)
        if self._recent_interfaces:
            dwell_times.append(max(now - self._interface_started_at, 0.0))
        mean_dwell = (
            sum(dwell_times) / len(dwell_times) if dwell_times else 0.0
        )
        dwell_cv = (
            statistics.pstdev(dwell_times) / mean_dwell
            if len(dwell_times) > 1 and mean_dwell
            else 0.0
        )
        similarities = self._interface_similarities
        switches = self._interface_switch_count
        return {
            "interface_switch_frequency_per_minute": round(
                switches / max(duration / 60.0, 1 / 60.0),
                2,
            ),
            "interface_average_semantic_similarity": round(
                sum(similarities) / len(similarities)
                if similarities
                else 1.0,
                4,
            ),
            "interface_average_dwell_seconds": round(mean_dwell, 2),
            "interface_dwell_time_cv": round(dwell_cv, 4),
            "interface_dwell_stability": round(
                max(0.0, 1.0 - min(dwell_cv, 1.0)),
                4,
            ),
            "interface_short_dwell_ratio": round(
                (
                    sum(value < 10 for value in dwell_times)
                    / len(dwell_times)
                )
                if dwell_times
                else 0.0,
                4,
            ),
            "interface_return_count": self._interface_return_count,
            "interface_return_rate": round(
                self._interface_return_count / switches
                if switches
                else 0.0,
                4,
            ),
            "interface_return_patterns": list(
                self._interface_return_examples
            ),
            "interface_change_origin_counts": dict(
                self._interface_origin_counts
            ),
        }

    def _record_mouse_activity_locked(self, now: float) -> None:
        if self._first_mouse_activity_at is None:
            self._first_mouse_activity_at = now
        previous = self._last_mouse_activity_at
        if previous is not None:
            gap = max(now - previous, 0.0)
            self._mouse_active_seconds += min(
                gap,
                _MOUSE_ACTIVE_TAIL_SECONDS,
            )
            if gap > _MOUSE_ACTIVE_TAIL_SECONDS:
                self._mouse_idle_gaps.append(
                    gap - _MOUSE_ACTIVE_TAIL_SECONDS
                )
        self._last_mouse_activity_at = now

    def _record_direction_locked(self, dx: float, dy: float) -> None:
        if dx == 0 and dy == 0:
            return
        angle = (math.atan2(dy, dx) + 2 * math.pi) % (2 * math.pi)
        bucket = int(
            angle / (2 * math.pi) * _DIRECTION_BUCKET_COUNT
        ) % _DIRECTION_BUCKET_COUNT
        self._direction_counts[bucket] += 1

    def _mouse_metrics_locked(
        self,
        now: float,
        duration: float,
    ) -> dict[str, float | int]:
        active_seconds = self._mouse_active_seconds
        idle_gaps = list(self._mouse_idle_gaps)
        if self._last_mouse_activity_at is None:
            idle_gaps = [duration] if duration else []
        else:
            leading_gap = max(
                (self._first_mouse_activity_at or self._window_started_at)
                - self._window_started_at,
                0.0,
            )
            if leading_gap:
                idle_gaps.insert(0, leading_gap)
            tail = max(now - self._last_mouse_activity_at, 0.0)
            active_seconds += min(tail, _MOUSE_ACTIVE_TAIL_SECONDS)
            if tail > _MOUSE_ACTIVE_TAIL_SECONDS:
                idle_gaps.append(tail - _MOUSE_ACTIVE_TAIL_SECONDS)
        active_seconds = min(active_seconds, duration)

        minutes = max(duration / 60.0, 1 / 60.0)
        displacement = (
            math.dist(self._mouse_start_position, self._last_mouse_position)
            if self._mouse_start_position is not None
            and self._last_mouse_position is not None
            else 0.0
        )
        direction_total = sum(self._direction_counts)
        entropy = 0.0
        if direction_total > 1:
            for count in self._direction_counts:
                if count:
                    probability = count / direction_total
                    entropy -= probability * math.log2(probability)
            entropy /= math.log2(_DIRECTION_BUCKET_COUNT)
        return {
            "mouse_active_ratio": round(
                active_seconds / duration if duration else 0.0,
                4,
            ),
            "mouse_movement_rate_per_minute": round(
                self._mouse_move_count / minutes,
                2,
            ),
            "mouse_click_rate_per_minute": round(
                self._mouse_click_count / minutes,
                2,
            ),
            "mouse_scroll_rate_per_minute": round(
                self._scrolling_count / minutes,
                2,
            ),
            "mouse_idle_gap_count": len(idle_gaps),
            "mouse_average_idle_gap_seconds": round(
                sum(idle_gaps) / len(idle_gaps) if idle_gaps else 0.0,
                2,
            ),
            "mouse_longest_idle_gap_seconds": round(
                max(idle_gaps, default=0.0),
                2,
            ),
            "mouse_average_velocity_pixels_per_second": round(
                self._mouse_distance / active_seconds
                if active_seconds
                else 0.0,
                2,
            ),
            "mouse_trajectory_efficiency": round(
                min(displacement / self._mouse_distance, 1.0)
                if self._mouse_distance
                else 0.0,
                4,
            ),
            "mouse_directional_entropy": round(entropy, 4),
        }

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
        now = self.monotonic()
        with self._lock:
            self._mouse_move_count += move_delta
            self._mouse_click_count += click_delta
            self._scrolling_count += scroll_delta
            if move_delta or click_delta or scroll_delta:
                self._record_mouse_activity_locked(now)
            if move_delta:
                old_position = previous["position"]
                new_position = current["position"]
                dx = new_position[0] - old_position[0]
                dy = new_position[1] - old_position[1]
                self._mouse_distance += math.hypot(dx, dy)
                if self._mouse_start_position is None:
                    self._mouse_start_position = old_position
                self._last_mouse_position = new_position
                self._record_direction_locked(dx, dy)
