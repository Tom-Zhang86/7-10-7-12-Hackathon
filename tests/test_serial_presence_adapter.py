import unittest
from unittest.mock import patch

from application.presence.serial_adapter import SerialPresenceAdapter
from events.event_types import PresenceDetected, PresenceLost


class FakeAPI:
    def __init__(self) -> None:
        self.events = []

    def post_event(self, event) -> None:
        self.events.append(event)


class SerialPresenceAdapterTest(unittest.TestCase):
    def test_converts_messages_and_suppresses_duplicates(self) -> None:
        api = FakeAPI()
        adapter = SerialPresenceAdapter(api)

        self.assertFalse(adapter._handle_line("READY\n"))
        self.assertTrue(adapter._handle_line("PRESENT\n"))
        self.assertFalse(adapter._handle_line("PRESENT\n"))
        self.assertTrue(adapter._handle_line("ABSENT\n"))
        self.assertFalse(adapter._handle_line("garbage\n"))

        self.assertEqual(len(api.events), 2)
        self.assertIsInstance(api.events[0], PresenceDetected)
        self.assertIsInstance(api.events[1], PresenceLost)

    def test_discovers_supported_macos_usb_port(self) -> None:
        adapter = SerialPresenceAdapter(FakeAPI())
        with patch(
            "application.presence.serial_adapter.glob",
            side_effect=[
                ["/dev/cu.usbserial-B"],
                ["/dev/cu.usbmodem-A"],
                [],
                [],
            ],
        ):
            self.assertEqual(adapter.discover_port(), "/dev/cu.usbmodem-A")

    def test_manual_pause_ignores_sensor_presence_until_resumed(self) -> None:
        api = FakeAPI()
        adapter = SerialPresenceAdapter(api)
        adapter._handle_line("PRESENT")

        adapter.pause()
        adapter._handle_line("ABSENT")
        adapter._handle_line("PRESENT")
        adapter.resume()

        self.assertEqual(
            [type(event) for event in api.events],
            [
                PresenceDetected,
                PresenceLost,
                PresenceLost,
                PresenceDetected,
            ],
        )
        self.assertFalse(adapter.manually_paused)

    def test_explicit_port_takes_priority(self) -> None:
        adapter = SerialPresenceAdapter(
            FakeAPI(),
            port="/dev/cu.custom-esp32",
        )
        self.assertEqual(adapter.discover_port(), "/dev/cu.custom-esp32")


if __name__ == "__main__":
    unittest.main()
