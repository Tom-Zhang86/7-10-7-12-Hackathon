from typing import Protocol

from application.activity.models import ActivityObservation


class ActivitySource(Protocol):
    """Internal watcher contract; not part of the system-layer public API."""

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def capture(self) -> ActivityObservation | None: ...
