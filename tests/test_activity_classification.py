from datetime import datetime, timedelta, timezone
import json
import unittest

from application.activity import ActivitySegment
from application.classification import (
    ActivityClassificationService,
    ConfigurableClassificationClient,
    RuleClassifier,
)
from application.providers import ProviderSelection


class MemorySettings:
    def load(self):
        return ProviderSelection("openai", "gpt-4o-mini")

    def get_api_key(self, _provider_id):
        return "test-key"


class MemoryStore:
    def __init__(self) -> None:
        self.cached = None

    def get_segment(self, _segment_hash):
        return self.cached


def segment(evidence: dict, category: str = "unknown") -> ActivitySegment:
    start = datetime(2026, 7, 23, 15, 0, tzinfo=timezone.utc)
    return ActivitySegment(
        session_id=1,
        start=start,
        end=start + timedelta(minutes=5),
        presence_state="present",
        activity_type="browser",
        category=category,
        confidence=0,
        evidence=evidence,
        source_event_ids=(1,),
        classifier_version="unclassified",
        segment_hash="segment-1",
    )


class RuleClassifierTest(unittest.TestCase):
    def test_classifies_known_learning_domain_and_developer_app(self) -> None:
        rules = RuleClassifier()
        learning = rules.classify(
            segment({"url": "https://coursera.org/learn/math"})
        )
        work = rules.classify(segment({"app": "Code"}))

        self.assertEqual(learning.category, "learning")
        self.assertEqual(work.category, "work")

    def test_leaves_ambiguous_youtube_unknown_without_remote(self) -> None:
        service = ActivityClassificationService(MemoryStore())
        result = service.classify(
            segment({"url": "https://youtube.com/watch", "page_title": "Video"})
        )

        self.assertEqual(result.category, "unknown")
        self.assertEqual(result.classifier_version, service.VERSION)


class ConfigurableClassificationClientTest(unittest.TestCase):
    def test_uses_existing_provider_selection_with_structured_schema(self) -> None:
        calls = []

        def transport(url, headers, payload, timeout):
            calls.append((url, headers, payload, timeout))
            value = {
                "category": "learning",
                "activity_type": "educational_video",
                "confidence": 0.88,
                "reason": "Course metadata",
            }
            return {
                "output": [
                    {
                        "content": [
                            {"type": "output_text", "text": json.dumps(value)}
                        ]
                    }
                ]
            }

        client = ConfigurableClassificationClient(
            MemorySettings(),
            transport=transport,
        )
        result = client.classify({"page_title": "Linear Algebra Lecture"})

        self.assertEqual(result.category, "learning")
        self.assertEqual(calls[0][1]["Authorization"], "Bearer test-key")
        self.assertEqual(
            calls[0][2]["text"]["format"]["name"],
            "ai_desk_activity_classification",
        )

