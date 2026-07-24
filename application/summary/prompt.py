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
2. behavior_metrics 是程序从 AI 分类后的连续时间窗确定性计算出的用户指标。
   最终展示层会提取：今日有效专注、专注率、平均专注、最长专注、分心次数、
   平均恢复时间。不得自行改算这些数值。
3. 你的输出用于内部分析，不会直接展示。深入分析数据，但不要输出证据链、
   内部分类标签、置信度、模型思考、研究文献、专家姓名或引用。
4. 使用直接、肯定、自然的表达，例如“平均每次专注 X”“平均每次分心 X”。
   不要使用“可能”“似乎”“推测”“难以判断”“AMBIGUOUS”“UNCERTAIN”。
5. completed 字段描述当天实际观察到的活动，不虚构文件、代码或任务成果。
6. 明日建议应具体、简短，并与今日活动或专注情况相关。
7. 不要输出输入中可能包含的密码、令牌或个人敏感信息。
8. attention_windows 是匿名的逐时间窗原始聚合指标。你必须在内部综合鼠标、
   键盘、前台界面、切换模式和在位传感器，然后只输出用户可采取行动的结论。
9. focus_assessment 只陈述 behavior_metrics 的六个核心指标，不添加对用户、
   工作内容、专注节奏或分心原因的描述。
10. 不得声称知道用户输入了什么；系统从未记录实际按键或鼠标坐标。
11. 不能仅凭短暂无输入认定走神。无法归类的时间不要强行计入专注或分心；
    但也不要在给用户的日报中讨论这种内部不确定性。
12. data_quality_note 只写简短的隐私说明，不显示降级、局限、推理或分类细节。
13. 使用简体中文，并严格遵守给定的 JSON Schema。"""

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
        "estimated_seconds 是依据采样间隔估算的近似值。"
        "先在内部深入分析 attention_windows，再使用 behavior_metrics 中已经计算"
        "好的确定性时长生成面向用户的最终结论；不要输出分析过程或内部标签。"
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
