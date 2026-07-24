import json
from pathlib import Path
import tempfile
import unittest

from application.providers import (
    ConfigurableLLMClient,
    ProviderSelection,
    ProviderSettings,
)
from application.providers.client import AnthropicMessagesClient, GeminiClient

from tests.test_daily_summary import VALID_SUMMARY


class MemorySecrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, provider_id: str) -> str | None:
        return self.values.get(provider_id)

    def set(self, provider_id: str, api_key: str) -> None:
        self.values[provider_id] = api_key

    def delete(self, provider_id: str) -> None:
        self.values.pop(provider_id, None)


class ProviderSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.secrets = MemorySecrets()
        self.settings = ProviderSettings(
            Path(self.temp_dir.name) / "settings.json",
            self.secrets,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_persists_only_provider_and_model(self) -> None:
        selection = ProviderSelection("anthropic", "claude-sonnet-5")
        self.settings.save_api_key("anthropic", "secret-value")
        self.settings.save(selection)

        self.assertEqual(self.settings.load(), selection)
        saved = (Path(self.temp_dir.name) / "settings.json").read_text()
        self.assertNotIn("secret-value", saved)
        self.assertEqual(self.settings.get_api_key("anthropic"), "secret-value")

    def test_invalid_or_unknown_settings_fall_back_safely(self) -> None:
        self.settings.path.write_text(
            '{"provider_id":"openai","model_id":"does-not-exist"}'
        )
        self.assertEqual(
            self.settings.load(),
            ProviderSelection("openai", "gpt-5.4-mini"),
        )

    def test_configurable_client_uses_current_selection_each_time(self) -> None:
        self.settings.save_api_key("openai", "openai-key")
        self.settings.save(ProviderSelection("openai", "gpt-4o-mini"))

        def transport(_url, _headers, _payload, _timeout):
            return {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(VALID_SUMMARY),
                            }
                        ],
                    }
                ]
            }

        client = ConfigurableLLMClient(self.settings, transport)
        self.assertEqual(client.generate({}), VALID_SUMMARY)
        self.assertEqual(client.source_name, "openai:gpt-4o-mini")


class ProviderClientTest(unittest.TestCase):
    def test_anthropic_structured_output(self) -> None:
        calls = []

        def transport(url, headers, payload, timeout):
            calls.append((url, headers, payload, timeout))
            return {
                "stop_reason": "end_turn",
                "content": [
                    {"type": "text", "text": json.dumps(VALID_SUMMARY)}
                ],
            }

        client = AnthropicMessagesClient(
            "key",
            "claude-sonnet-5",
            transport=transport,
        )
        self.assertEqual(client.generate({}), VALID_SUMMARY)
        self.assertEqual(calls[0][1]["x-api-key"], "key")
        self.assertEqual(
            calls[0][2]["output_config"]["format"]["type"],
            "json_schema",
        )

    def test_gemini_structured_output(self) -> None:
        calls = []

        def transport(url, headers, payload, timeout):
            calls.append((url, headers, payload, timeout))
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": json.dumps(VALID_SUMMARY)}
                            ]
                        }
                    }
                ]
            }

        client = GeminiClient(
            "key",
            "gemini-3.5-flash",
            transport=transport,
        )
        self.assertEqual(client.generate({}), VALID_SUMMARY)
        self.assertEqual(calls[0][1]["x-goog-api-key"], "key")
        self.assertEqual(
            calls[0][2]["generationConfig"]["responseMimeType"],
            "application/json",
        )


if __name__ == "__main__":
    unittest.main()
