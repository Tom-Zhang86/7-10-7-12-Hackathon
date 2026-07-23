from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest

from application.activity import MacOSAccessibilitySource
from application.context import MacOSAccessibilityProvider


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 23, 15, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


class MacOSAccessibilityTest(unittest.TestCase):
    def test_parses_bounded_deduplicated_accessibility_labels(self) -> None:
        provider = MacOSAccessibilityProvider(
            platform_name="Darwin",
            runner=lambda _command, _timeout: SimpleNamespace(
                returncode=0,
                stdout="Code\x1fmain.py\x1fEditor\x1eRun\x1eEditor\n",
                stderr="",
            ),
        )

        context = provider.capture()

        self.assertEqual(context.app, "Code")
        self.assertEqual(context.texts, ("Editor", "Run"))

    def test_source_throttles_expensive_accessibility_capture(self) -> None:
        clock = FakeClock()
        calls = []

        class Provider:
            def capture(self):
                calls.append(True)
                return SimpleNamespace(
                    as_payload=lambda: {"app": "Code", "accessibility_text": []}
                )

        source = MacOSAccessibilitySource(
            Provider(),
            min_interval_seconds=15,
            clock=clock,
        )
        source.start()

        self.assertIsNotNone(source.capture())
        clock.now += timedelta(seconds=5)
        self.assertIsNone(source.capture())
        clock.now += timedelta(seconds=10)
        self.assertIsNotNone(source.capture())
        self.assertEqual(len(calls), 2)

    def test_source_throttles_failed_capture_attempts(self) -> None:
        clock = FakeClock()
        calls = []

        class Provider:
            def capture(self):
                calls.append(True)
                raise RuntimeError("permission denied")

        source = MacOSAccessibilitySource(
            Provider(),
            min_interval_seconds=15,
            clock=clock,
        )

        with self.assertRaises(RuntimeError):
            source.capture()
        clock.now += timedelta(seconds=5)
        self.assertIsNone(source.capture())
        self.assertEqual(len(calls), 1)
