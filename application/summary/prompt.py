import json
import re
from typing import Any


SUMMARY_JSON_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "name": "ai_desk_daily_summary",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "headline": {"type": "string"},
            "completed": {
                "type": "array",
                "items": {"type": "string"},
            },
            "work_duration_summary": {"type": "string"},
            "focus_assessment": {"type": "string"},
            "activity_insights": {
                "type": "array",
                "items": {"type": "string"},
            },
            "tomorrow_suggestions": {
                "type": "array",
                "items": {"type": "string"},
            },
            "data_quality_note": {"type": "string"},
        },
        "required": [
            "headline",
            "completed",
            "work_duration_summary",
            "focus_assessment",
            "activity_insights",
            "tomorrow_suggestions",
            "data_quality_note",
        ],
        "additionalProperties": False,
    },
}


SYSTEM_PROMPT = """你是 AI Desk 的个人工作日报助手。

请严格根据用户提供的统计信息和电脑活动生成简洁的中文日报。

规则：
1. 工作时长、休息次数、Session 数和最长专注时间必须忠于输入数据。
2. 应用名称和窗口标题只能证明相关窗口曾处于前台，不能证明任务已经完成。
3. 对工作内容的推断使用“可能”“主要活动显示”等谨慎措辞。
4. completed 字段描述观察到的工作活动；没有足够证据时不要声称已经完成具体成果。
5. 如果上下文不足，明确说明无法判断具体工作内容。
6. 明日建议应具体、简短，并与今日活动或专注情况相关。
7. 不要输出输入中可能包含的密码、令牌或个人敏感信息。
8. 使用简体中文，并严格遵守给定的 JSON Schema。"""

_SENSITIVE_WINDOW_TERMS = (
    "1password",
    "keychain",
    "password",
    "密码",
    "bank",
    "银行",
    "incognito",
    "private browsing",
    "无痕",
)
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{12,}\b", re.IGNORECASE),
)


def _redact_text(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted[:300]


def sanitize_daily_data(daily_data: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe copy with likely sensitive window data removed."""

    sanitized = json.loads(json.dumps(daily_data, ensure_ascii=False))
    for block in sanitized.get("activity_blocks", []):
        app = str(block.get("app", ""))
        title = str(block.get("window_title", ""))
        searchable = f"{app} {title}".lower()
        if any(term in searchable for term in _SENSITIVE_WINDOW_TERMS):
            block["window_title"] = "[已隐藏敏感窗口标题]"
        else:
            block["window_title"] = _redact_text(title)
        block["app"] = _redact_text(app)
    return sanitized


def build_user_prompt(daily_data: dict[str, Any]) -> str:
    """Serialize Phase 3 output as the sole factual basis for the model."""

    sanitized = sanitize_daily_data(daily_data)
    return (
        "请根据以下 AI Desk 当日数据生成日报。"
        "estimated_seconds 是依据采样间隔估算的近似值。\n\n"
        + json.dumps(
            sanitized,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
