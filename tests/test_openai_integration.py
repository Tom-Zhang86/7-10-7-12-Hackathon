import os
import unittest

from application.config import load_application_environment
from application.summary import DailySummary, OpenAIResponsesClient


RUN_OPENAI_INTEGRATION = os.getenv("RUN_OPENAI_INTEGRATION") == "1"
if RUN_OPENAI_INTEGRATION:
    load_application_environment()


@unittest.skipUnless(
    RUN_OPENAI_INTEGRATION,
    "Set RUN_OPENAI_INTEGRATION=1 to call the real OpenAI API.",
)
class OpenAIIntegrationTest(unittest.TestCase):
    """Opt-in network test; never runs during the normal test suite."""

    def test_generates_structured_daily_summary(self) -> None:
        self.assertTrue(
            os.getenv("OPENAI_API_KEY"),
            "OPENAI_API_KEY must be configured in .env or the shell.",
        )
        client = OpenAIResponsesClient()
        result = client.generate(
            {
                "stats": {
                    "total_work_seconds": 3600,
                    "session_count": 1,
                    "break_count": 1,
                    "longest_focus_seconds": 1800,
                },
                "sessions": [],
                "breaks": [],
                "activity_blocks": [
                    {
                        "start": "2026-07-09T14:00:00+00:00",
                        "end": "2026-07-09T14:30:00+00:00",
                        "app": "Code",
                        "window_title": "application/summary/service.py",
                        "estimated_seconds": 1800,
                        "session_id": 1,
                    }
                ],
                "frequent_apps": [
                    {"app": "Code", "estimated_seconds": 1800}
                ],
                "context_event_count": 1,
            }
        )

        summary = DailySummary.from_dict(result)
        self.assertTrue(summary.headline)
        self.assertTrue(summary.work_duration_summary)


if __name__ == "__main__":
    unittest.main()
