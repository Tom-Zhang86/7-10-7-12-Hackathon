import dataclasses
import unittest
from datetime import datetime, timezone

from application.owner_handoff.classification.activity_classifier import (
    ActivityClassifier,
)
from application.owner_handoff.domain.activity import (
    ActivityClassification,
    ActivitySample,
)


def _sample(**overrides) -> ActivitySample:
    defaults = dict(
        observed_at=datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc),
        duration_seconds=60.0,
    )
    defaults.update(overrides)
    return ActivitySample(**defaults)


class ActivityClassifierWorkingTest(unittest.TestCase):
    def test_meaningful_interaction_with_task_app_is_working(self) -> None:
        sample = _sample(
            active_application="Terminal",
            keypress_count=12,
            mouse_click_count=2,
            interface_switch_count=1,
        )
        result = ActivityClassifier().classify(sample)
        self.assertEqual(result, ActivityClassification.WORKING)


class ActivityClassifierNotWorkingTest(unittest.TestCase):
    def test_no_active_interface_and_no_interaction_is_not_working(self) -> None:
        sample = _sample(active_application="")
        result = ActivityClassifier().classify(sample)
        self.assertEqual(result, ActivityClassification.NOT_WORKING)

    def test_locked_screen_with_no_interaction_is_not_working(self) -> None:
        sample = _sample(active_application="loginwindow")
        result = ActivityClassifier().classify(sample)
        self.assertEqual(result, ActivityClassification.NOT_WORKING)


class ActivityClassifierAmbiguousTest(unittest.TestCase):
    def test_low_interaction_is_ambiguous(self) -> None:
        sample = _sample(active_application="Terminal", keypress_count=1)
        result = ActivityClassifier().classify(sample)
        self.assertEqual(result, ActivityClassification.AMBIGUOUS)

    def test_rapid_unexplained_switching_is_ambiguous(self) -> None:
        sample = _sample(
            active_application="Terminal",
            keypress_count=10,
            interface_switch_count=9,
        )
        result = ActivityClassifier().classify(sample)
        self.assertEqual(result, ActivityClassification.AMBIGUOUS)

    def test_communication_app_stays_ambiguous_even_with_high_interaction(self) -> None:
        sample = _sample(
            active_application="Slack",
            keypress_count=20,
            mouse_click_count=10,
        )
        result = ActivityClassifier().classify(sample)
        self.assertEqual(result, ActivityClassification.AMBIGUOUS)

    def test_unknown_application_stays_ambiguous_even_with_high_interaction(self) -> None:
        sample = _sample(
            active_application="SomeRandomUnknownApp",
            keypress_count=20,
            mouse_click_count=10,
        )
        result = ActivityClassifier().classify(sample)
        self.assertEqual(result, ActivityClassification.AMBIGUOUS)


class ActivitySampleShapeTest(unittest.TestCase):
    """Structural guarantee: no field can hold a key value or coordinates."""

    def test_no_actual_key_or_coordinate_field_exists(self) -> None:
        field_names = {f.name for f in dataclasses.fields(ActivitySample)}
        forbidden_substrings = ("key_value", "keys_pressed", "position", "coordinate", "pointer_x", "pointer_y")
        for name in field_names:
            for forbidden in forbidden_substrings:
                self.assertNotIn(
                    forbidden,
                    name,
                    msg=f"ActivitySample field {name!r} looks like raw key/coordinate storage",
                )

    def test_chrome_domain_rejects_full_url(self) -> None:
        with self.assertRaises(ValueError):
            _sample(chrome_domain="https://example.com/path?x=1")

    def test_duration_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            _sample(duration_seconds=0)


if __name__ == "__main__":
    unittest.main()
