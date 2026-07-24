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


SYSTEM_PROMPT = """你是 AI Desk 的工作记录整理器，不是聊天助手或效率教练。

请严格根据用户提供的统计信息和电脑活动，生成简洁、具体、可核对的中文工作记录。

规则：
1. 工作时长、休息次数、Session 数和最长专注时间必须忠于输入数据。
2. 优先引用准确时间、应用名称和非空窗口标题；不要只罗列应用出现次数。
3. 应用和窗口处于前台不代表任务已经完成。completed 只描述观察到的活动，不得虚构成果。
4. focus_assessment 只描述连续工作和中断情况，不评价用户自律或效率。
5. activity_insights 最多两项，写清已知事实和仍无法确认的内容，避免反复使用“可能”“主要活动显示”。
6. tomorrow_suggestions 表示下一步，最多两项；禁止“继续保持专注”“提高效率”“合理安排时间”等通用建议。
7. headline 必须包含具体应用、窗口主题或活动组合，禁止“今日工作概览”等空标题。
8. 信息不足时只说明一次证据边界，不要在每个段落重复免责声明。
9. 不要输出密码、令牌、Cookie、私人消息内容或其他敏感信息。
10. 使用简体中文，并严格遵守给定的 JSON Schema。"""

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
        "请根据以下 AI Desk 当日数据生成可核对的工作记录。"
        "estimated_seconds 是依据采样间隔估算的近似值。\n\n"
        + json.dumps(
            sanitized,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
