import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from application.summary import (
    DailySummary,
    ManualSummaryService,
    OpenAIResponsesClient,
    SummaryStore,
)
from application.summary.llm_client import LLMClientError
from application.summary.prompt import build_user_prompt


VALID_SUMMARY = {
    "headline": "今天主要进行了应用层开发",
    "completed": ["主要活动显示正在开发日报模块"],
    "work_duration_summary": "今日累计工作 1 小时。",
    "focus_assessment": "最长连续专注 30 分钟。",
    "activity_insights": ["前台活动主要集中在 Code。"],
    "tomorrow_suggestions": ["在真实 Mac 上验证完整流程。"],
    "data_quality_note": "具体任务根据窗口活动推断。",
}


class FakeAggregator:
    def __init__(self) -> None:
        self.call_count = 0

    def build_today(self):
        self.call_count += 1
        return {
            "stats": {
                "total_work_seconds": 3600,
                "session_count": 1,
                "break_count": 1,
                "longest_focus_seconds": 1800,
            },
            "frequent_apps": [
                {"app": "Code", "estimated_seconds": 2400}
            ],
            "activity_blocks": [],
            "sessions": [],
            "breaks": [],
            "context_event_count": 3,
        }


class FakeLLM:
    source_name = "test:model"

    def __init__(self, result=None, error=None) -> None:
        self.result = result
        self.error = error
        self.call_count = 0

    def generate(self, _daily_data):
        self.call_count += 1
        if self.error:
            raise self.error
        return self.result


class OpenAIResponsesClientTest(unittest.TestCase):
    def test_sends_structured_response_request_and_parses_output(self) -> None:
        calls = []

        def transport(url, headers, payload, timeout):
            calls.append((url, headers, payload, timeout))
            return {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    VALID_SUMMARY,
                                    ensure_ascii=False,
                                ),
                            }
                        ],
                    }
                ]
            }

        client = OpenAIResponsesClient(
            api_key="test-key",
            model="test-model",
            base_url="https://example.test/v1",
            timeout_seconds=4,
            transport=transport,
        )
        result = client.generate({"stats": {}})

        self.assertEqual(result, VALID_SUMMARY)
        url, headers, payload, timeout = calls[0]
        self.assertEqual(url, "https://example.test/v1/responses")
        self.assertEqual(headers["Authorization"], "Bearer test-key")
        self.assertEqual(payload["model"], "test-model")
        self.assertEqual(
            payload["text"]["format"]["type"],
            "json_schema",
        )
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertFalse(payload["store"])
        self.assertEqual(timeout, 4)

    def test_requires_key_and_handles_refusal(self) -> None:
        client = OpenAIResponsesClient(
            api_key="",
            transport=lambda *_args: {},
            load_environment=False,
        )
        with self.assertRaisesRegex(LLMClientError, "OPENAI_API_KEY"):
            client.generate({})

        refused = OpenAIResponsesClient(
            api_key="test-key",
            transport=lambda *_args: {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "refusal", "refusal": "Cannot comply"}
                        ],
                    }
                ]
            },
        )
        with self.assertRaisesRegex(LLMClientError, "refused"):
            refused.generate({})


class DailySummaryModelTest(unittest.TestCase):
    def test_validates_required_fields_and_types(self) -> None:
        summary = DailySummary.from_dict(VALID_SUMMARY)
        self.assertEqual(summary.headline, VALID_SUMMARY["headline"])

        invalid = dict(VALID_SUMMARY)
        invalid.pop("headline")
        with self.assertRaisesRegex(ValueError, "headline"):
            DailySummary.from_dict(invalid)

        invalid = dict(VALID_SUMMARY)
        invalid["completed"] = "not a list"
        with self.assertRaisesRegex(TypeError, "completed"):
            DailySummary.from_dict(invalid)

    def test_prompt_redacts_sensitive_window_titles_and_tokens(self) -> None:
        prompt = build_user_prompt(
            {
                "activity_blocks": [
                    {
                        "app": "1Password",
                        "window_title": "password for bank",
                    },
                    {
                        "app": "Terminal",
                        "window_title": (
                            "export OPENAI_API_KEY="
                            + "sk-"
                            + "abcdefghijklmnop"
                        ),
                    },
                ]
            }
        )

        self.assertNotIn("password for bank", prompt)
        self.assertNotIn("sk-" + "abcdefghijklmnop", prompt)
        self.assertIn("[已隐藏敏感窗口标题]", prompt)
        self.assertIn("[REDACTED]", prompt)


class ManualSummaryServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SummaryStore(Path(self.temp_dir.name) / "summaries")
        self.clock = lambda: datetime(
            2026,
            7,
            9,
            20,
            0,
            tzinfo=timezone.utc,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_generates_only_when_called_and_persists_llm_result(self) -> None:
        aggregator = FakeAggregator()
        llm = FakeLLM(result=VALID_SUMMARY)
        service = ManualSummaryService(
            aggregator,
            llm,
            self.store,
            clock=self.clock,
        )

        self.assertEqual(aggregator.call_count, 0)
        self.assertEqual(llm.call_count, 0)

        generation = service.generate_today()

        self.assertEqual(aggregator.call_count, 1)
        self.assertEqual(llm.call_count, 1)
        self.assertEqual(generation.source, "test:model")
        self.assertIsNone(generation.warning)
        loaded = self.store.load(generation.target_date)
        self.assertEqual(loaded, generation)
        service.close()

    def test_retries_then_uses_and_persists_fallback(self) -> None:
        llm = FakeLLM(error=LLMClientError("network unavailable"))
        service = ManualSummaryService(
            FakeAggregator(),
            llm,
            self.store,
            retry_count=1,
            clock=self.clock,
        )

        with self.assertLogs(
            "application.summary.service",
            level="WARNING",
        ):
            generation = service.generate_today()

        self.assertEqual(llm.call_count, 2)
        self.assertEqual(generation.source, "fallback")
        self.assertIn("network unavailable", generation.warning)
        self.assertIn("本地规则", generation.summary.data_quality_note)
        self.assertEqual(
            self.store.load(generation.target_date),
            generation,
        )
        service.close()

    def test_async_manual_generation_returns_future(self) -> None:
        service = ManualSummaryService(
            FakeAggregator(),
            FakeLLM(result=VALID_SUMMARY),
            self.store,
            clock=self.clock,
        )

        future = service.generate_today_async()
        generation = future.result(timeout=2)

        self.assertEqual(generation.summary.headline, VALID_SUMMARY["headline"])
        service.close()


if __name__ == "__main__":
    unittest.main()
