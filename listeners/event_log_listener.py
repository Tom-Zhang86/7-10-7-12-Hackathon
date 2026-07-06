from pathlib import Path
from threading import RLock

from events.event_types import Event


class EventLogListener:
    """Append every runtime event to a daily text log."""

    def __init__(self, log_dir: str | Path = "logs") -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def __call__(self, event: Event) -> None:
        log_path = self.log_dir / f"{event.timestamp.date().isoformat()}.log"
        line = self._format_event(event)

        with self._lock:
            with log_path.open("a", encoding="utf-8") as file:
                file.write(line + "\n")

    @staticmethod
    def _format_event(event: Event) -> str:
        timestamp = event.timestamp.astimezone().strftime("%H:%M:%S")
        payload = ""
        if event.payload:
            payload = f" {event.payload}"
        return f"{timestamp} {event.name}{payload}"
