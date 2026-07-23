import json
from typing import Any


CLASSIFICATION_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "name": "ai_desk_activity_classification",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["learning", "work", "entertainment", "unknown"],
            },
            "activity_type": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string"},
        },
        "required": ["category", "activity_type", "confidence", "reason"],
        "additionalProperties": False,
    },
}


CLASSIFICATION_SYSTEM_PROMPT = """你是 AI Desk 的活动分类器。
只根据提供的应用、网页元数据和媒体状态，把活动分为 learning、work、
entertainment 或 unknown。YouTube 等平台不能仅凭域名判断；证据不足必须返回
unknown。不要推断任务已经完成，不要复述敏感数据，严格输出给定 JSON Schema。"""


def build_classification_prompt(evidence: dict[str, Any]) -> str:
    return (
        "请分类下面这一个活动时间段。\n"
        + json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
    )
