from datetime import datetime, timedelta, timezone
import unittest

from application.activity import (
    ActivityCoordinator,
    ActivityObservation,
    ActivityPrivacyPolicy,
    HeartbeatReducer,
    MacOSWindowSource,
)
from application.context import DesktopContext


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 23, 15, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def observation(
    clock: FakeClock,
    app: str,
    bucket_id: str = "window-test",
) -> ActivityObservation:
    return ActivityObservation(
        timestamp=clock(),
        bucket_id=bucket_id,
        event_type="currentwindow",
        source="macos_active_window",
        data={"app": app, "window_title": "Desk"},
    )


class HeartbeatReducerTest(unittest.TestCase):
    def test_merges_equal_data_and_splits_changes_and_long_gaps(self) -> None:
        clock = FakeClock()
        reducer = HeartbeatReducer(pulsetime_seconds=10)

        first = reducer.ingest(observation(clock, "Code"))
        self.assertEqual(first[0].duration_seconds, 0)

        clock.advance(5)
        merged = reducer.ingest(observation(clock, "Code"))
        self.assertEqual(merged[0].duration_seconds, 5)

        clock.advance(2)
        changed = reducer.ingest(observation(clock, "Safari"))
        self.assertEqual(len(changed), 2)
        self.assertEqual(changed[0].data["app"], "Code")
        self.assertEqual(changed[0].duration_seconds, 7)
        self.assertEqual(changed[1].data["app"], "Safari")

        clock.advance(30)
        after_gap = reducer.ingest(observation(clock, "Safari"))
        self.assertEqual(after_gap[0].duration_seconds, 10)
        self.assertEqual(after_gap[1].duration_seconds, 0)

    def test_tracks_buckets_independently(self) -> None:
        clock = FakeClock()
        reducer = HeartbeatReducer()
        reducer.ingest(observation(clock, "Code", "window"))
        reducer.ingest(observation(clock, "Docs", "browser"))

        clock.advance(5)
        reducer.ingest(observation(clock, "Code", "window"))
        spans = reducer.flush(clock())

        self.assertEqual({span.bucket_id for span in spans}, {"window", "browser"})
        durations = {span.bucket_id: span.duration_seconds for span in spans}
        self.assertEqual(durations["window"], 5)
        self.assertEqual(durations["browser"], 5)


class SequenceSource:
    def __init__(self, values) -> None:
        self.values = iter(values)
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def capture(self):
        return next(self.values)


class FakeStore:
    def __init__(self) -> None:
        self.spans = []

    def upsert_span(self, span) -> int:
        self.spans.append(span)
        return len(self.spans)


class FakeAPI:
    def __init__(self) -> None:
        self.records = []

    def record_context_event(self, **record):
        self.records.append(record)
        return record


class ActivityCoordinatorTest(unittest.TestCase):
    def test_persists_spans_and_keeps_legacy_context_output(self) -> None:
        clock = FakeClock()
        values = []
        for seconds in (0, 5, 65):
            clock.now = datetime(
                2026, 7, 23, 15, 0, tzinfo=timezone.utc
            ) + timedelta(seconds=seconds)
            values.append(observation(clock, "Code"))
        source = SequenceSource(values)
        api = FakeAPI()
        store = FakeStore()
        coordinator = ActivityCoordinator(
            api,
            [source],
            store,
            compatibility_heartbeat_seconds=60,
            clock=clock,
        )

        self.assertEqual(coordinator.capture_once(9), 1)
        self.assertEqual(coordinator.capture_once(9), 1)
        self.assertEqual(coordinator.capture_once(9), 1)

        # Equal observations update the in-memory span; SQLite only receives
        # the first sample and the configured persistence heartbeat.
        self.assertEqual(len(store.spans), 3)
        self.assertEqual(len(api.records), 2)
        self.assertTrue(all(row["session_id"] == 9 for row in api.records))
        self.assertEqual(api.records[0]["payload"]["app"], "Code")

    def test_sanitizes_obvious_secret_fields(self) -> None:
        clock = FakeClock()
        raw = observation(clock, "Code")
        raw = ActivityObservation(
            timestamp=raw.timestamp,
            bucket_id=raw.bucket_id,
            event_type=raw.event_type,
            source=raw.source,
            data={"app": "Code", "token": "do-not-store"},
        )
        api = FakeAPI()
        store = FakeStore()
        coordinator = ActivityCoordinator(
            api,
            [SequenceSource([raw])],
            store,
            clock=clock,
        )

        coordinator.capture_once(1)

        self.assertNotIn("token", store.spans[0].data)
        self.assertNotIn("token", api.records[0]["payload"])

    def test_privacy_pause_skips_expensive_source_capture(self) -> None:
        class CountingSource:
            calls = 0

            def capture(self):
                self.calls += 1
                raise AssertionError("paused source should not be called")

        source = CountingSource()
        coordinator = ActivityCoordinator(
            FakeAPI(),
            [source],
            FakeStore(),
            privacy_policy=ActivityPrivacyPolicy(paused=True),
        )

        self.assertEqual(coordinator.capture_once(1), 0)
        self.assertEqual(source.calls, 0)


class MacOSWindowSourceTest(unittest.TestCase):
    def test_adapts_existing_provider_to_internal_observation(self) -> None:
        clock = FakeClock()

        class Provider:
            def capture(self):
                return DesktopContext("Code", "activity.py")

        source = MacOSWindowSource(
            Provider(),
            bucket_id="window-host",
            clock=clock,
        )
        value = source.capture()

        self.assertEqual(value.bucket_id, "window-host")
        self.assertEqual(value.event_type, "currentwindow")
        self.assertEqual(
            value.data,
            {"app": "Code", "window_title": "activity.py"},
        )


if __name__ == "__main__":
    unittest.main()
