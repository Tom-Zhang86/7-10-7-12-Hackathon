import json
from typing import Any
from urllib.parse import quote

from application.classification.models import ClassificationDecision
from application.classification.prompt import (
    CLASSIFICATION_SCHEMA,
    CLASSIFICATION_SYSTEM_PROMPT,
    build_classification_prompt,
)
from application.providers.settings import ProviderSettings
from application.summary.llm_client import JsonTransport, LLMClientError, _post_json


def _decode(text: str, provider: str) -> ClassificationDecision:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMClientError(f"{provider} returned invalid classification JSON.") from exc
    if not isinstance(value, dict):
        raise LLMClientError(f"{provider} classification was not an object.")
    return ClassificationDecision.from_dict(value, f"llm:{provider.lower()}")


class ConfigurableClassificationClient:
    """Use the existing provider selection and Keychain for classification."""

    def __init__(
        self,
        settings: ProviderSettings,
        transport: JsonTransport = _post_json,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    def classify(self, evidence: dict[str, Any]) -> ClassificationDecision:
        selection = self.settings.load()
        api_key = self.settings.get_api_key(selection.provider_id)
        if not api_key:
            raise LLMClientError("No API key is configured for classification.")
        prompt = build_classification_prompt(evidence)
        if selection.provider_id == "openai":
            return self._openai(api_key, selection.model_id, prompt)
        if selection.provider_id == "anthropic":
            return self._anthropic(api_key, selection.model_id, prompt)
        if selection.provider_id == "google":
            return self._google(api_key, selection.model_id, prompt)
        raise LLMClientError("Unsupported classification provider.")

    def _openai(
        self,
        api_key: str,
        model: str,
        prompt: str,
    ) -> ClassificationDecision:
        response = self.transport(
            "https://api.openai.com/v1/responses",
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            {
                "model": model,
                "input": [
                    {"role": "system", "content": CLASSIFICATION_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "text": {"format": CLASSIFICATION_SCHEMA},
                "max_output_tokens": 300,
                "store": False,
            },
            self.timeout_seconds,
        )
        for output in response.get("output", []):
            for content in output.get("content", []):
                if content.get("type") == "output_text":
                    return _decode(str(content.get("text") or ""), "OpenAI")
        raise LLMClientError("OpenAI classification contained no output text.")

    def _anthropic(
        self,
        api_key: str,
        model: str,
        prompt: str,
    ) -> ClassificationDecision:
        response = self.transport(
            "https://api.anthropic.com/v1/messages",
            {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            {
                "model": model,
                "max_tokens": 300,
                "system": CLASSIFICATION_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}],
                "output_config": {
                    "format": {
                        "type": "json_schema",
                        "schema": CLASSIFICATION_SCHEMA["schema"],
                    }
                },
            },
            self.timeout_seconds,
        )
        for block in response.get("content", []):
            if block.get("type") == "text":
                return _decode(str(block.get("text") or ""), "Anthropic")
        raise LLMClientError("Anthropic classification contained no text.")

    def _google(
        self,
        api_key: str,
        model: str,
        prompt: str,
    ) -> ClassificationDecision:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{quote(model, safe='')}:generateContent"
        )
        response = self.transport(
            url,
            {"x-goog-api-key": api_key, "Content-Type": "application/json"},
            {
                "system_instruction": {
                    "parts": [{"text": CLASSIFICATION_SYSTEM_PROMPT}]
                },
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseJsonSchema": CLASSIFICATION_SCHEMA["schema"],
                    "maxOutputTokens": 300,
                },
            },
            self.timeout_seconds,
        )
        try:
            parts = response["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMClientError("Google classification contained no candidate.") from exc
        text = "".join(str(part.get("text") or "") for part in parts)
        return _decode(text, "Google")
