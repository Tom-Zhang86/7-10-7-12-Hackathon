from dataclasses import dataclass
from glob import glob
import logging
from threading import Event as ThreadEvent
from threading import Lock, Thread
from typing import Any, Callable

from events.event_types import PresenceDetected, PresenceLost

try:
    import serial
except ImportError:  # pragma: no cover - handled as a user-facing status
    serial = None


logger = logging.getLogger(__name__)


DEFAULT_PORT_PATTERNS = (
    "/dev/cu.usbserial*",
    "/dev/cu.usbmodem*",
    "/dev/cu.SLAB_USBtoUART*",
    "/dev/cu.wchusbserial*",
)

PRESENT_MESSAGES = {"PRESENT", "OCCUPIED", "1", "HIGH"}
ABSENT_MESSAGES = {"ABSENT", "CLEAR", "0", "LOW"}


@dataclass(frozen=True)
class SerialConnectionStatus:
    """Thread-safe status snapshot consumed by the dashboard."""

    state: str
    port: str | None = None
    detail: str = ""

    @property
    def connected(self) -> bool:
        return self.state == "connected"


class SerialPresenceAdapter:
    """Convert ESP32 USB serial messages into runtime presence events.

    The ESP32 firmware emits ``PRESENT`` and ``ABSENT`` lines. The adapter
    discovers common macOS USB serial device names, reconnects after a cable
    interruption, and suppresses duplicate readings before posting events.
    """

    def __init__(
        self,
        api: Any,
        port: str | None = None,
        baudrate: int = 115200,
        reconnect_seconds: float = 2.0,
        port_patterns: tuple[str, ...] = DEFAULT_PORT_PATTERNS,
        serial_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.api = api
        self.port = port
        self.baudrate = baudrate
        self.reconnect_seconds = reconnect_seconds
        self.port_patterns = port_patterns
        self.serial_factory = serial_factory

        self._stop_signal = ThreadEvent()
        self._thread: Thread | None = None
        self._lock = Lock()
        self._status = SerialConnectionStatus("stopped")
        self._last_presence: bool | None = None
        self._manually_paused = False
        self._serial_connection: Any | None = None

    @property
    def status(self) -> SerialConnectionStatus:
        with self._lock:
            return self._status

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def manually_paused(self) -> bool:
        with self._lock:
            return self._manually_paused

    def pause(self) -> None:
        """Start a break and ignore PRESENT readings until resumed."""

        with self._lock:
            if self._manually_paused:
                return
            self._manually_paused = True
        self.api.post_event(PresenceLost())

    def resume(self) -> None:
        """Resume work even when the person never left the sensor."""

        with self._lock:
            if not self._manually_paused:
                return
            self._manually_paused = False
        self.api.post_event(PresenceDetected())

    def start(self) -> None:
        if self.is_running:
            return
        if serial is None and self.serial_factory is None:
            self._set_status(
                "error",
                detail="缺少 pyserial；请安装 requirements.txt",
            )
            logger.error("pyserial is not installed; serial adapter disabled.")
            return

        self._stop_signal.clear()
        self._thread = Thread(
            target=self._run,
            name="serial-presence-adapter",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_signal.set()
        connection = self._serial_connection
        if connection is not None:
            try:
                connection.close()
            except Exception:
                logger.debug("Serial connection close failed.", exc_info=True)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(self.reconnect_seconds + 1, 3))
        self._thread = None
        self._serial_connection = None
        self._set_status("stopped")

    def reset_presence_state(self) -> None:
        """Forget prior readings and manual pause state after a full reset."""

        with self._lock:
            self._last_presence = None
            self._manually_paused = False

    def discover_port(self) -> str | None:
        if self.port:
            return self.port

        candidates = {
            candidate
            for pattern in self.port_patterns
            for candidate in glob(pattern)
        }
        return sorted(candidates)[0] if candidates else None

    def _run(self) -> None:
        while not self._stop_signal.is_set():
            port = self.discover_port()
            if port is None:
                self._set_status("waiting", detail="等待 ESP32 USB 串口")
                self._stop_signal.wait(self.reconnect_seconds)
                continue

            try:
                self._read_port(port)
            except Exception as exc:
                if not self._stop_signal.is_set():
                    logger.warning("Serial presence connection failed: %s", exc)
                    self._set_status("error", port, str(exc))
            finally:
                self._close_connection()

            if not self._stop_signal.is_set():
                self._stop_signal.wait(self.reconnect_seconds)

    def _read_port(self, port: str) -> None:
        factory = self.serial_factory or serial.Serial
        connection = factory(port=port, baudrate=self.baudrate, timeout=1)
        self._serial_connection = connection
        self._last_presence = None
        self._set_status("connected", port, "ESP32 已连接")
        logger.info("ESP32 presence sensor connected on %s.", port)

        while not self._stop_signal.is_set():
            raw_line = connection.readline()
            if not raw_line:
                continue
            if isinstance(raw_line, bytes):
                line = raw_line.decode("utf-8", errors="ignore")
            else:
                line = str(raw_line)
            self._handle_line(line)

    def _handle_line(self, line: str) -> bool:
        message = line.strip().upper()
        if message in PRESENT_MESSAGES:
            present = True
        elif message in ABSENT_MESSAGES:
            present = False
        else:
            if message and message != "READY":
                logger.debug("Ignoring ESP32 serial line: %s", message)
            return False

        if present == self._last_presence:
            return False
        self._last_presence = present
        if present and self.manually_paused:
            logger.debug("Ignoring PRESENT while manually paused.")
            return False
        event = PresenceDetected() if present else PresenceLost()
        self.api.post_event(event)
        logger.info("ESP32 presence state: %s", message)
        return True

    def _close_connection(self) -> None:
        connection = self._serial_connection
        self._serial_connection = None
        if connection is None:
            return
        try:
            connection.close()
        except Exception:
            logger.debug("Serial connection close failed.", exc_info=True)

    def _set_status(
        self,
        state: str,
        port: str | None = None,
        detail: str = "",
    ) -> None:
        with self._lock:
            self._status = SerialConnectionStatus(state, port, detail)
