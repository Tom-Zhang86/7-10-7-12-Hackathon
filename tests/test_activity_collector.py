import unittest

from application.activity import ActivityMetricsCollector
from application.context import DesktopContext


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeListener:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class FakeAPI:
    def __init__(self) -> None:
        self.records = []

    def record_context_event(self, **record):
        self.records.append(record)
        return record


class FakeKey:
    def __init__(self, name: str) -> None:
        self.name = name

    def __str__(self) -> str:
        return f"Key.{self.name}"


class ActivityMetricsCollectorTest(unittest.TestCase):
    def test_persists_aggregate_metrics_and_classifications_only(self) -> None:
        clock = FakeClock()
        api = FakeAPI()
        listeners = (FakeListener(), FakeListener())
        collector = ActivityMetricsCollector(
            api,
            window_seconds=600,
            current_task="Implement Python API",
            listener_factory=lambda *_callbacks: listeners,
            monotonic=clock,
        )
        collector.start(7)
        collector.observe_interface(
            DesktopContext("Visual Studio Code", "analyzer.py")
        )
        for _ in range(30):
            collector.record_keypress()
        collector.record_mouse_move(10, 10)
        collector.record_mouse_move(20, 20)
        collector.record_mouse_click()
        collector.record_mouse_click()
        collector.record_mouse_click()
        collector.record_scroll()
        clock.advance(60)

        payload = collector.flush()
        collector.stop()

        self.assertIsNotNone(payload)
        self.assertEqual(api.records[0]["source"], "attention_window")
        self.assertEqual(api.records[0]["session_id"], 7)
        self.assertEqual(payload["keypress_count"], 30)
        self.assertEqual(payload["attention_state"], "FOCUSED_ACTIVE")
        self.assertAlmostEqual(payload["mouse_active_ratio"], 0.0333)
        self.assertEqual(payload["mouse_movement_rate_per_minute"], 2.0)
        self.assertEqual(payload["mouse_click_rate_per_minute"], 3.0)
        self.assertEqual(payload["mouse_scroll_rate_per_minute"], 1.0)
        self.assertEqual(payload["mouse_idle_gap_count"], 1)
        self.assertEqual(payload["mouse_longest_idle_gap_seconds"], 58.0)
        self.assertGreater(
            payload["mouse_average_velocity_pixels_per_second"],
            0,
        )
        self.assertAlmostEqual(payload["mouse_trajectory_efficiency"], 1.0)
        self.assertEqual(payload["mouse_directional_entropy"], 0.0)
        self.assertNotIn("keys", payload)
        self.assertNotIn("mouse_positions", payload)
        self.assertTrue(all(listener.stopped for listener in listeners))

    def test_keyboard_metrics_store_categories_and_timing_not_keys(self) -> None:
        clock = FakeClock()
        api = FakeAPI()
        collector = ActivityMetricsCollector(
            api,
            window_seconds=600,
            listener_factory=lambda *_callbacks: (
                FakeListener(),
                FakeListener(),
            ),
            monotonic=clock,
        )
        collector.start(3)
        collector.record_keypress(FakeKey("a"))
        clock.advance(0.5)
        collector.record_keypress(FakeKey("a"))
        clock.advance(1.5)
        collector.record_keypress(FakeKey("backspace"))
        collector.record_keypress(FakeKey("cmd"))
        collector.record_keypress(FakeKey("c"))
        collector.record_key_release(FakeKey("cmd"))
        clock.advance(58)

        payload = collector.flush()
        collector.stop()

        self.assertEqual(payload["keystrokes_per_minute"], 4.0)
        self.assertEqual(payload["average_typing_burst_length"], 4.0)
        self.assertEqual(payload["correction_count"], 1)
        self.assertEqual(payload["shortcut_count"], 1)
        self.assertEqual(payload["repetition_count"], 1)
        self.assertEqual(
            payload["typing_pause_distribution"]["under_1s"],
            2,
        )
        self.assertEqual(
            payload["typing_pause_distribution"]["1_to_3s"],
            1,
        )
        self.assertNotIn("a", payload)
        self.assertNotIn("key_sequence", payload)

    def test_interface_metrics_capture_similarity_dwell_return_and_origin(
        self,
    ) -> None:
        clock = FakeClock()
        collector = ActivityMetricsCollector(
            FakeAPI(),
            window_seconds=600,
            listener_factory=lambda *_callbacks: (
                FakeListener(),
                FakeListener(),
            ),
            monotonic=clock,
        )
        collector.start(4)
        linkedin = DesktopContext(
            "Google Chrome",
            "LinkedIn",
            "LinkedIn",
            "https://www.linkedin.com",
        )
        collector.observe_interface(linkedin)
        clock.advance(4)
        collector.record_mouse_click()
        clock.advance(1)
        collector.observe_interface(DesktopContext("Python", "main.py"))
        clock.advance(16)
        collector.observe_interface(linkedin)

        payload = collector.flush()
        collector.stop()

        self.assertGreater(
            payload["interface_switch_frequency_per_minute"],
            0,
        )
        self.assertEqual(
            payload["interface_average_semantic_similarity"],
            0,
        )
        self.assertGreater(payload["interface_dwell_time_cv"], 0)
        self.assertEqual(payload["interface_return_count"], 1)
        self.assertGreater(payload["interface_return_rate"], 0)
        self.assertEqual(
            payload["interface_change_origin_counts"],
            {
                "LIKELY_USER_INITIATED": 1,
                "LIKELY_AUTOMATIC": 1,
                "UNKNOWN": 0,
            },
        )


if __name__ == "__main__":
    unittest.main()
