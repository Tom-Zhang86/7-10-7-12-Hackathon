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

请严格根据用户提供的数据和 concentration_rubric_assessment 生成中文日报。

规则：
1. 必须遵循上下文时间序列量表，不得使用“无输入=分心”“有输入=专注”
   或“切换界面=分心”等简单规则。
2. 区分六种潜在状态：专注生产、专注阅读/思考、专注任务支持切换、
   主动分心、被动分心、离开。量表给出的概率不得自行改算。
3. 分开说明认知投入和目标一致性。高鼠标/键盘活动只说明交互投入，
   不自动表示活动符合用户的工作目标。
4. 联合分析鼠标结构、键盘节奏、界面语义连续性、切换频率、停留稳定性、
   返回结构、切换来源、在位传感器和时间序列，不能孤立解释单一信号。
5. 短暂无输入可能是阅读、思考、观看相关内容或等待，不能直接判为走神。
6. 语义相关的界面切换和快速返回可能支持同一任务；自动界面变化几乎不提供
   注意力证据；高频、低相似度、短停留且不返回才是较强的碎片化证据。
7. 在内部完整应用量表后，只选择概率最高的一种状态作为最终结论。
   focus_assessment 必须直接、确定地陈述该状态，不显示概率、置信度、
   不确定性、证据过程或其他候选状态，不使用“可能”“似乎”“推测”。
8. 个人和任务特定基线的限制只保留在内部，不向用户展示。
9. 工作时长等事实必须忠于输入。不得虚构成果、输入内容或鼠标坐标。
10. 不输出密码、令牌、敏感内容、隐藏思维过程、研究文献或专家引用。
11. data_quality_note 写简短的隐私与局限说明。
12. 使用简体中文，并严格遵守给定的 JSON Schema。"""

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
    for window in sanitized.get("attention_windows", []):
        window["recent_interfaces"] = [
            (
                "[已隐藏敏感界面]"
                if any(
                    term in str(interface).lower()
                    for term in _SENSITIVE_WINDOW_TERMS
                )
                else _redact_text(str(interface))
            )
            for interface in window.get("recent_interfaces", [])
        ]
        window["interface_return_patterns"] = [
            _redact_text(str(pattern))
            for pattern in window.get("interface_return_patterns", [])
        ]
    return sanitized


def build_user_prompt(daily_data: dict[str, Any]) -> str:
    """Serialize Phase 3 output as the sole factual basis for the model."""

    sanitized = sanitize_daily_data(daily_data)
    return (
        "请根据以下 AI Desk 当日数据生成日报。"
        "estimated_seconds 是依据采样间隔估算的近似值。"
        "concentration_rubric_assessment 是应用依据指定量表完成的时间序列概率"
        "评估，不得改算。内部选择概率最高的状态，最终只给出确定、简洁的"
        "单一结论，不显示概率、不确定性、证据过程或其他候选状态。"
        "输出必须是单个 JSON 对象，并且必须完整包含这 7 个键："
        "headline、completed、work_duration_summary、focus_assessment、"
        "activity_insights、tomorrow_suggestions、data_quality_note。"
        "completed、activity_insights、tomorrow_suggestions 必须是字符串数组，"
        "其余字段必须是字符串。不要遗漏任何键。\n\n"
        + json.dumps(
            sanitized,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
