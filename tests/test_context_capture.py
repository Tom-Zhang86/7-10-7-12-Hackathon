from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest

from application.context import (
    ContextCaptureError,
    ContextCollector,
    DesktopContext,
    MacOSContextProvider,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 9, 14, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


class FakeContextAPI:
    def __init__(self) -> None:
        self.records = []

    def record_context_event(self, **record):
        self.records.append(record)
        return record


class SequenceProvider:
    def __init__(self, values) -> None:
        self.values = iter(values)

    def capture(self):
        return next(self.values)


class MacOSContextProviderTest(unittest.TestCase):
    def test_parses_frontmost_app_and_window_title(self) -> None:
        calls = []

        def runner(command, timeout):
            calls.append((command, timeout))
            return SimpleNamespace(
                returncode=0,
                stdout="Code\x1fmain.py - AI Desk\n",
                stderr="",
            )

        provider = MacOSContextProvider(
            runner=runner,
            timeout_seconds=1.5,
            platform_name="Darwin",
        )

        context = provider.capture()

        self.assertEqual(context, DesktopContext("Code", "main.py - AI Desk"))
        self.assertEqual(calls[0][0][0], "/usr/bin/osascript")
        self.assertEqual(calls[0][1], 1.5)

    def test_rejects_non_macos_and_permission_errors(self) -> None:
        provider = MacOSContextProvider(platform_name="Windows")
        with self.assertRaises(ContextCaptureError):
            provider.capture()

        denied = MacOSContextProvider(
            platform_name="Darwin",
            runner=lambda _command, _timeout: SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="System Events got an error: not authorized",
            ),
        )
        with self.assertRaisesRegex(ContextCaptureError, "not authorized"):
            denied.capture()


class ContextCollectorTest(unittest.TestCase):
    def test_records_changes_and_heartbeat_but_deduplicates_samples(self) -> None:
        api = FakeContextAPI()
        clock = FakeClock()
        provider = SequenceProvider(
            [
                DesktopContext("Code", "main.py"),
                DesktopContext("Code", "main.py"),
                DesktopContext("Code", "main.py"),
                DesktopContext("Safari", "Docs"),
            ]
        )
        collector = ContextCollector(
            api,
            provider,
            heartbeat_seconds=60,
            clock=clock,
        )

        self.assertTrue(collector.capture_once(7))
        clock.advance(10)
        self.assertFalse(collector.capture_once(7))
        clock.advance(50)
        self.assertTrue(collector.capture_once(7))
        clock.advance(1)
        self.assertTrue(collector.capture_once(7))

        self.assertEqual(len(api.records), 3)
        self.assertTrue(
            all(record["session_id"] == 7 for record in api.records)
        )
        self.assertEqual(
            api.records[-1]["payload"],
            {"app": "Safari", "window_title": "Docs"},
        )

    def test_truncates_large_payload_fields(self) -> None:
        api = FakeContextAPI()
        collector = ContextCollector(
            api,
            SequenceProvider([DesktopContext("A" * 150, "T" * 300)]),
            max_title_length=20,
        )

        collector.capture_once(1)

        self.assertEqual(len(api.records[0]["payload"]["app"]), 120)
        self.assertEqual(len(api.records[0]["payload"]["window_title"]), 20)


if __name__ == "__main__":
    unittest.main()
