import unittest

from application.summary.rubric import assess_concentration


class ConcentrationRubricTest(unittest.TestCase):
    def test_relevant_active_work_favors_focused_production(self) -> None:
        assessment = assess_concentration(
            {
                "attention_windows": [
                    {
                        "timestamp": "2026-07-25T10:00:00Z",
                        "duration_seconds": 60,
                        "presence_sensor": True,
                        "mouse_state": "ACTIVE",
                        "keyboard_state": "ACTIVE_TYPING",
                        "content_state": "TASK_RELATED",
                        "switching_pattern": "STABLE",
                    }
                ]
            }
        )

        probabilities = assessment["state_probabilities"]
        self.assertEqual(
            assessment["dominant_state"],
            "FOCUSED_PRODUCTION",
        )
        self.assertGreater(assessment["concentration_probability"], 0.6)
        self.assertGreater(assessment["goal_alignment_probability"], 0.8)
        self.assertAlmostEqual(sum(probabilities.values()), 1.0, places=3)

    def test_inactivity_on_relevant_content_is_not_automatic_distraction(
        self,
    ) -> None:
        assessment = assess_concentration(
            {
                "attention_windows": [
                    {
                        "duration_seconds": 60,
                        "presence_sensor": True,
                        "mouse_state": "NO_ACTIVITY",
                        "keyboard_state": "NO_TYPING",
                        "content_state": "TASK_RELATED",
                        "switching_pattern": "STABLE",
                    }
                ]
            }
        )

        self.assertEqual(
            assessment["dominant_state"],
            "FOCUSED_RECEPTION_REASONING",
        )

    def test_active_entertainment_separates_engagement_from_alignment(
        self,
    ) -> None:
        assessment = assess_concentration(
            {
                "attention_windows": [
                    {
                        "duration_seconds": 60,
                        "presence_sensor": True,
                        "mouse_state": "ACTIVE",
                        "keyboard_state": "ACTIVE_TYPING",
                        "content_state": "ENTERTAINMENT",
                        "switching_pattern": "STABLE",
                    }
                ]
            }
        )

        self.assertEqual(
            assessment["dominant_state"],
            "ACTIVE_DISTRACTION",
        )
        self.assertLess(assessment["goal_alignment_probability"], 0.2)


if __name__ == "__main__":
    unittest.main()
