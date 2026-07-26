"""Wearable adapter boundary (Master Spec section 6).

The BLE implementation is deferred because final firmware details are not
yet available. Do not invent final BLE UUIDs. A future real
``WearableTransport`` implementation still needs, and this module invents
none of:

- BLE Service UUID;
- Characteristic UUIDs;
- payload encoding;
- device provisioning;
- RSSI/proximity policy;
- reconnect behavior;
- replay protection (beyond the question_id/expiry/duplicate checks already
  enforced at the domain/lifecycle layer — a real BLE transport will need
  its own protocol-level replay protection in addition to those);
- button debounce behavior.

Only the interface and a deterministic simulator exist here.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Callable

from application.owner_handoff.domain.presence import (
    WearableAnswer,
    WearableButton,
    WearableProximitySample,
    WearableProximityState,
)
from utils.time_utils import utc_now


class WearableTransportError(RuntimeError):
    """Raised when an operation is attempted on a disconnected wearable."""


class WearableTransport:
    """Boundary a real BLE wearable adapter must implement.

    (Documented as a concrete base class rather than a ``Protocol`` so the
    method list below is self-describing; a real implementation should
    subclass or structurally match it.)
    """

    def read_proximity(self) -> WearableProximitySample:
        raise NotImplementedError

    def send_question(self, question_id: str, letters: tuple[str, ...]) -> None:
        raise NotImplementedError

    def poll_answer(self) -> WearableAnswer | None:
        raise NotImplementedError


class WearableSimulator(WearableTransport):
    """Deterministic wearable simulator for tests and simulator-mode use.

    - Supports explicitly setting owner proximity.
    - ``send_question`` receives only a question_id and available letters —
      never the full question text, context summary, or any WorkContext
      field. What was last sent is recorded (``last_sent_payload``) purely
      so tests can assert on the minimal payload shape.
    - Queued answers are returned deterministically, one per ``poll_answer``
      call — no timing, threads, or real BLE/network access of any kind.
    """

    def __init__(
        self,
        device_id: str,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not device_id or not device_id.strip():
            raise ValueError("device_id must be a non-empty string")
        self._device_id = device_id
        self._clock = clock
        self._proximity_state = WearableProximityState.UNKNOWN
        self._last_sent_payload: dict | None = None
        self._queued_answers: deque[WearableAnswer] = deque()
        self._connected = True

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def last_sent_payload(self) -> dict | None:
        return self._last_sent_payload

    @property
    def connected(self) -> bool:
        return self._connected

    def set_proximity(self, state: WearableProximityState) -> None:
        if not isinstance(state, WearableProximityState):
            raise TypeError("state must be a WearableProximityState")
        self._proximity_state = state

    def read_proximity(self) -> WearableProximitySample:
        # Repair: a disconnected wearable must never continue reporting
        # whatever proximity state was last set (e.g. stale OWNER_AWAY) --
        # it reads as UNKNOWN, the same as "no signal," until reconnected.
        state = (
            self._proximity_state if self._connected else WearableProximityState.UNKNOWN
        )
        return WearableProximitySample(
            state=state,
            observed_at=self._clock(),
            device_id=self._device_id,
        )

    def send_question(self, question_id: str, letters: tuple[str, ...]) -> None:
        if not self._connected:
            raise WearableTransportError("wearable disconnected")
        if not question_id or not question_id.strip():
            raise ValueError("question_id must be a non-empty string")
        _validate_letters(letters)
        # Deliberately only these two fields — see class docstring.
        self._last_sent_payload = {
            "question_id": question_id,
            "letters": tuple(letters),
        }

    def queue_answer(
        self,
        button: WearableButton,
        *,
        question_id: str | None = None,
        timestamp: datetime | None = None,
        device_id: str | None = None,
    ) -> None:
        """Queue one deterministic answer for the next ``poll_answer`` call.

        Defaults ``question_id`` to whatever was last sent via
        ``send_question`` (the common case in tests) and ``timestamp`` to
        the injected clock's current time.
        """

        resolved_question_id = (
            question_id
            if question_id is not None
            else (self._last_sent_payload or {}).get("question_id")
        )
        if not resolved_question_id:
            raise ValueError(
                "question_id must be provided (no question has been sent yet)"
            )
        self._queued_answers.append(
            WearableAnswer(
                device_id=device_id if device_id is not None else self._device_id,
                question_id=resolved_question_id,
                button=button,
                timestamp=timestamp if timestamp is not None else self._clock(),
            )
        )

    def poll_answer(self) -> WearableAnswer | None:
        if not self._connected:
            return None
        if self._queued_answers:
            return self._queued_answers.popleft()
        return None

    def disconnect(self) -> None:
        self._connected = False
        # Repair: any answer queued before this disconnect must never
        # become deliverable after a later reconnect -- discard it now.
        self._queued_answers.clear()

    def reconnect(self) -> None:
        self._connected = True


_HANDOFF_SELECTION_LETTERS = frozenset({"A", "B", "C", "D"})
_PERMISSION_LETTERS = frozenset({"YES", "NO"})


def _validate_letters(letters: tuple[str, ...]) -> None:
    """Enforce the wearable payload's letter contract: non-empty, no
    duplicates, and drawn entirely from exactly one button family — never
    mixed, never an arbitrary string."""

    if not letters:
        raise ValueError("letters must not be empty")
    if len(set(letters)) != len(letters):
        raise ValueError("letters must not contain duplicates")
    letter_set = frozenset(letters)
    if letter_set <= _HANDOFF_SELECTION_LETTERS or letter_set <= _PERMISSION_LETTERS:
        return
    raise ValueError(
        "letters must be entirely from {A, B, C, D} or entirely from "
        "{YES, NO}, never mixed or arbitrary"
    )
