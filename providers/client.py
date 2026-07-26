import json
from typing import Any
from urllib.parse import quote

from application.providers.settings import ProviderSettings
from application.summary.llm_client import (
    JsonTransport,
    LLMClientError,
    OpenAIResponsesClient,
    _get_json,
    _post_json,
)
from application.summary.prompt import (
    SUMMARY_JSON_SCHEMA,
    SYSTEM_PROMPT,
    build_user_prompt,
)


def _decode_json_object(text: str, provider: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMClientError(f"{provider} returned invalid summary JSON.") from exc
    if not isinstance(value, dict):
        raise LLMClientError(f"{provider} summary was not a JSON object.")
    return value


_SUMMARY_OUTPUT_TEMPLATE = {
    "headline": "字符串",
    "completed": ["字符串"],
    "work_duration_summary": "字符串",
    "focus_assessment": "字符串",
    "activity_insights": ["字符串"],
    "tomorrow_suggestions": ["字符串"],
    "data_quality_note": "字符串",
}


class AnthropicMessagesClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        transport: JsonTransport = _post_json,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.url = "https://api.anthropic.com/v1/messages"

    @property
    def source_name(self) -> str:
        return f"anthropic:{self.model}"

    def generate(self, daily_data: dict[str, Any]) -> dict[str, Any]:
        response = self.transport(
            self.url,
            {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            {
                "model": self.model,
                "max_tokens": 1600,
                "system": SYSTEM_PROMPT,
                "messages": [
                    {"role": "user", "content": build_user_prompt(daily_data)}
                ],
                "output_config": {
                    "format": {
                        "type": "json_schema",
                        "schema": SUMMARY_JSON_SCHEMA["schema"],
                    }
                },
            },
            self.timeout_seconds,
        )
        if response.get("stop_reason") == "refusal":
            raise LLMClientError("Anthropic refused the summary request.")
        for block in response.get("content", []):
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                return _decode_json_object(block["text"], "Anthropic")
        raise LLMClientError("Anthropic response contained no text.")


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        transport: JsonTransport = _post_json,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    @property
    def source_name(self) -> str:
        return f"google:{self.model}"

    def generate(self, daily_data: dict[str, Any]) -> dict[str, Any]:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{quote(self.model, safe='')}:generateContent"
        )
        response = self.transport(
            url,
            {
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
            },
            {
                "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": build_user_prompt(daily_data)}],
                    }
                ],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseJsonSchema": SUMMARY_JSON_SCHEMA["schema"],
                    "maxOutputTokens": 1600,
                },
            },
            self.timeout_seconds,
        )
        try:
            parts = response["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMClientError("Google Gemini response contained no candidate.") from exc
        text = "".join(
            part.get("text", "")
            for part in parts
            if isinstance(part, dict)
        )
        return _decode_json_object(text, "Google Gemini")


class DeepSeekChatClient:
    """DeepSeek Chat Completions client with JSON output enabled."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        transport: JsonTransport = _post_json,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.url = "https://api.deepseek.com/chat/completions"

    @property
    def source_name(self) -> str:
        return f"deepseek:{self.model}"

    def generate(self, daily_data: dict[str, Any]) -> dict[str, Any]:
        response = self.transport(
            self.url,
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            f"{SYSTEM_PROMPT}\n"
                            "只输出一个有效 JSON 对象，不要 Markdown。必须严格使用"
                            "以下完整结构，不得改名或遗漏任何字段：\n"
                            f"{json.dumps(_SUMMARY_OUTPUT_TEMPLATE, ensure_ascii=False)}\n"
                            "保持精炼：每个列表最多 4 项，每项最多 120 个汉字；"
                            "activity_insights 写 2 至 4 项，总输出不要超过 1800 个汉字。"
                        ),
                    },
                    {"role": "user", "content": build_user_prompt(daily_data)},
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": 4000,
                "stream": False,
            },
            self.timeout_seconds,
        )
        try:
            text = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMClientError(
                "DeepSeek response contained no completion."
            ) from exc
        if not isinstance(text, str) or not text.strip():
            raise LLMClientError("DeepSeek response contained no text.")
        if response["choices"][0].get("finish_reason") == "length":
            raise LLMClientError(
                "DeepSeek summary exceeded the output limit; retry the request."
            )
        return _decode_json_object(text, "DeepSeek")


class ConfigurableLLMClient:
    """Resolve the active provider and Keychain secret at request time."""

    def __init__(
        self,
        settings: ProviderSettings,
        post_transport: JsonTransport = _post_json,
    ) -> None:
        self.settings = settings
        self.post_transport = post_transport
        self._last_source = "llm"

    @property
    def source_name(self) -> str:
        return self._last_source

    def _build_client(self) -> Any:
        selection = self.settings.load()
        api_key = self.settings.get_api_key(selection.provider_id)
        if not api_key:
            raise LLMClientError(
                "尚未连接 AI 服务。请点击“AI 设置”添加 API Key。"
            )
        if selection.provider_id == "openai":
            return OpenAIResponsesClient(
                api_key=api_key,
                model=selection.model_id,
                transport=self.post_transport,
                load_environment=False,
            )
        if selection.provider_id == "deepseek":
            return DeepSeekChatClient(
                api_key,
                selection.model_id,
                transport=self.post_transport,
            )
        if selection.provider_id == "anthropic":
            return AnthropicMessagesClient(
                api_key,
                selection.model_id,
                transport=self.post_transport,
            )
        if selection.provider_id == "google":
            return GeminiClient(
                api_key,
                selection.model_id,
                transport=self.post_transport,
            )
        raise LLMClientError("Unsupported AI provider.")

    def generate(self, daily_data: dict[str, Any]) -> dict[str, Any]:
        client = self._build_client()
        result = client.generate(daily_data)
        self._last_source = client.source_name
        return result

    def validate(
        self,
        provider_id: str,
        api_key: str,
        timeout_seconds: float = 12.0,
    ) -> None:
        if provider_id == "openai":
            url = "https://api.openai.com/v1/models"
            headers = {"Authorization": f"Bearer {api_key}"}
        elif provider_id == "deepseek":
            url = "https://api.deepseek.com/models"
            headers = {"Authorization": f"Bearer {api_key}"}
        elif provider_id == "anthropic":
            url = "https://api.anthropic.com/v1/models?limit=1"
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            }
        elif provider_id == "google":
            url = "https://generativelanguage.googleapis.com/v1beta/models"
            headers = {"x-goog-api-key": api_key}
        else:
            raise LLMClientError("Unsupported AI provider.")
        _get_json(url, headers, timeout_seconds)
