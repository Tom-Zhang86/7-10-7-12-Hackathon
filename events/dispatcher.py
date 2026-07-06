from collections import defaultdict
from collections.abc import Callable
from threading import RLock

from events.event_types import Event

EventListener = Callable[[Event], None]


class EventDispatcher:
    """Observer registry used by runtime, AI modules, UI, and loggers."""

    WILDCARD = "*"

    def __init__(self) -> None:
        self._listeners: dict[str, list[EventListener]] = defaultdict(list)
        self._lock = RLock()

    def subscribe(self, event_name: str, listener: EventListener) -> None:
        """Register a listener for an event name or '*' for all events."""

        with self._lock:
            if listener not in self._listeners[event_name]:
                self._listeners[event_name].append(listener)

    def unsubscribe(self, event_name: str, listener: EventListener) -> None:
        """Remove a listener if it is currently registered."""

        with self._lock:
            listeners = self._listeners[event_name]
            if listener in listeners:
                listeners.remove(listener)

    def publish(self, event: Event) -> None:
        """Notify listeners without allowing one listener to block the rest."""

        with self._lock:
            listeners = list(self._listeners[event.name])
            listeners += list(self._listeners[self.WILDCARD])

        for listener in listeners:
            listener(event)
