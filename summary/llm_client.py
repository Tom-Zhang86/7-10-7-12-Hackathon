from collections.abc import Callable
import json
import os
import ssl
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import certifi

from application.config import load_application_environment
from application.summary.prompt import (
    SUMMARY_JSON_SCHEMA,
    SYSTEM_PROMPT,
    build_user_prompt,
)


class LLMClientError(RuntimeError):
    """Raised when a remote summary could not be generated."""


JsonTransport = Callable[
    [str, dict[str, str], dict[str, Any], float],
    dict[str, Any],
]


def _read_json_response(request: Request, timeout: float) -> dict[str, Any]:
    # python.org Framework builds on macOS may not have a populated system
    # OpenSSL certificate file. Use certifi's maintained CA bundle while
    # retaining normal hostname and certificate verification.
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    try:
        with urlopen(
            request,
            timeout=timeout,
            context=ssl_context,
        ) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise LLMClientError(
            f"AI provider returned HTTP {exc.code}: {detail[:500]}"
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise LLMClientError(f"AI provider request failed: {exc}") from exc

    try:
        value = json.loads(body)
    except json.JSONDecodeError as exc:
        raise LLMClientError("AI provider returned invalid JSON.") from exc
    if not isinstance(value, dict):
        raise LLMClientError("AI provider returned an unexpected response.")
    return value


def _post_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    return _read_json_response(request, timeout)


def _get_json(
    url: str,
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    return _read_json_response(
        Request(url, headers=headers, method="GET"),
        timeout,
    )


class OpenAIResponsesClient:
    """Minimal dependency-free client for Responses API structured output."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = 30.0,
        transport: JsonTransport = _post_json,
        load_environment: bool = True,
    ) -> None:
        if api_key is None and load_environment:
            load_application_environment()
        self.api_key = (
            os.getenv("OPENAI_API_KEY") if api_key is None else api_key
        )
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
        root = base_url or os.getenv(
            "OPENAI_BASE_URL",
            "https://api.openai.com/v1",
        )
        self.url = (
            root.rstrip("/")
            if root.rstrip("/").endswith("/responses")
            else f"{root.rstrip('/')}/responses"
        )
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    @property
    def source_name(self) -> str:
        return f"openai:{self.model}"

    def generate(self, daily_data: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise LLMClientError("OPENAI_API_KEY is not configured.")

        payload = {
            "model": self.model,
            "input": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(daily_data)},
            ],
            "text": {"format": SUMMARY_JSON_SCHEMA},
            "max_output_tokens": 1600,
            "store": False,
        }
        response = self.transport(
            self.url,
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            payload,
            self.timeout_seconds,
        )
        text = self._extract_output_text(response)
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMClientError(
                "OpenAI response text was not valid JSON."
            ) from exc
        if not isinstance(value, dict):
            raise LLMClientError("OpenAI structured output was not an object.")
        return value

    @staticmethod
    def _extract_output_text(response: dict[str, Any]) -> str:
        for output in response.get("output", []):
            if output.get("type") != "message":
                continue
            for content in output.get("content", []):
                if content.get("type") == "refusal":
                    raise LLMClientError(
                        f"OpenAI refused the summary: {content.get('refusal')}"
                    )
                if content.get("type") == "output_text":
                    text = content.get("text")
                    if isinstance(text, str) and text:
                        return text
        raise LLMClientError("OpenAI response contained no output text.")
