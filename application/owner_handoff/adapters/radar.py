"""Desk radar adapter boundary (Master Spec section 6).

Phase 2 implements simulator/domain logic only — no real serial reader.
When a real radar implementation is eventually built, it MUST use one
serial-port owner/broker: two independent readers must never open the same
serial device concurrently. (This mirrors the existing, already-proven
pattern in ``application/presence/serial_adapter.py``, which owns its own
single serial connection — a future real ``RadarSensor`` should either
reuse that ownership model directly or coordinate through a single shared
broker; it must not spawn a second, independent reader against the same
port.) That real implementation, and the broker itself, remain unbuilt;
only the interface and a deterministic simulator exist here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Protocol

from application.owner_handoff.domain.presence import RadarSample, RadarState
from utils.time_utils import utc_now


class RadarSensor(Protocol):
    """Boundary a real radar adapter must implement."""

    def read(self) -> RadarSample:
        ...


class SimulatorRadar:
    """Deterministic radar simulator.

    Performs no threads, sleeps, serial access, or I/O of any kind — a test
    or simulator-mode caller sets the state explicitly and reads it back.
    """

    def __init__(
        self,
        *,
        initial_state: RadarState = RadarState.UNKNOWN,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._state = initial_state
        self._clock = clock

    def set_state(self, state: RadarState) -> None:
        if not isinstance(state, RadarState):
            raise TypeError("state must be a RadarState")
        self._state = state

    def read(self) -> RadarSample:
        return RadarSample(state=self._state, observed_at=self._clock())
