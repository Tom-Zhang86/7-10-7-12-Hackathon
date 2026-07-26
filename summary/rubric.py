from __future__ import annotations

from typing import Any


STATES = (
    "FOCUSED_PRODUCTION",
    "FOCUSED_RECEPTION_REASONING",
    "FOCUSED_TASK_TRANSITION",
    "ACTIVE_DISTRACTION",
    "PASSIVE_DISTRACTION",
    "AWAY",
)


def _normalize(scores: dict[str, float]) -> dict[str, float]:
    total = sum(max(value, 0.0) for value in scores.values()) or 1.0
    return {
        state: max(scores.get(state, 0.0), 0.0) / total
        for state in STATES
    }


def _window_probabilities(
    window: dict[str, Any],
    previous: dict[str, float] | None,
) -> dict[str, float]:
    """Apply the supplied rubric to one behavioral window."""

    scores = {state: 0.25 for state in STATES}
    duration = max(float(window.get("duration_seconds") or 0), 0.0)
    mouse = str(window.get("mouse_state") or "NO_ACTIVITY")
    keyboard = str(window.get("keyboard_state") or "NO_TYPING")
    content = str(window.get("content_state") or "AMBIGUOUS")
    switching = str(window.get("switching_pattern") or "UNKNOWN")
    presence = window.get("presence_sensor")
    interaction = mouse != "NO_ACTIVITY" or keyboard != "NO_TYPING"
    similarity = float(
        window.get("interface_average_semantic_similarity") or 0
    )
    frequency = float(
        window.get("interface_switch_frequency_per_minute") or 0
    )
    returns = int(window.get("interface_return_count") or 0)
    origin = window.get("interface_change_origin_counts") or {}
    likely_automatic = int(origin.get("LIKELY_AUTOMATIC") or 0)
    likely_user = int(origin.get("LIKELY_USER_INITIATED") or 0)

    if presence is False:
        scores["AWAY"] += 6.0 if not interaction else 2.0
    elif content == "TASK_RELATED":
        if interaction:
            scores["FOCUSED_PRODUCTION"] += 3.5
        else:
            # Inactivity on relevant content is ambiguous and decays gradually.
            scores["FOCUSED_RECEPTION_REASONING"] += (
                3.0 if duration <= 120 else 2.0 if duration <= 300 else 1.0
            )
            if duration > 300:
                scores["PASSIVE_DISTRACTION"] += min(duration / 300, 2.0)
    elif content == "ENTERTAINMENT":
        scores[
            "ACTIVE_DISTRACTION" if interaction else "PASSIVE_DISTRACTION"
        ] += 4.0
    elif content == "COMMUNICATION":
        # Communication may be work or distraction without a declared goal.
        scores["FOCUSED_PRODUCTION"] += 1.0 if interaction else 0.4
        scores["ACTIVE_DISTRACTION"] += 1.2 if interaction else 0.5
    elif interaction:
        scores["FOCUSED_PRODUCTION"] += 0.8
        scores["ACTIVE_DISTRACTION"] += 0.8
    else:
        scores["FOCUSED_RECEPTION_REASONING"] += 0.7
        scores["PASSIVE_DISTRACTION"] += 0.7

    # Automatic changes contribute almost no attention evidence.
    user_switch_weight = (
        likely_user / max(likely_user + likely_automatic, 1)
        if likely_user or likely_automatic
        else 1.0
    )
    if switching == "RELATED_SWITCHING":
        scores["FOCUSED_TASK_TRANSITION"] += (
            2.0 + similarity + min(returns, 2) * 0.4
        ) * user_switch_weight
    elif switching == "UNRELATED_SWITCHING":
        fragmentation = (
            1.5 + (1.0 - similarity) + min(frequency / 3.0, 2.0)
        )
        scores[
            "ACTIVE_DISTRACTION" if interaction else "PASSIVE_DISTRACTION"
        ] += fragmentation * user_switch_weight
    elif switching == "STABLE" and content == "TASK_RELATED":
        scores[
            "FOCUSED_PRODUCTION"
            if interaction
            else "FOCUSED_RECEPTION_REASONING"
        ] += 0.8

    # Mechanical/repetitive input is interaction, but weaker goal evidence.
    repetition = float(window.get("repetition_rate") or 0)
    correction = float(window.get("correction_rate") or 0)
    if repetition > 0.4:
        scores["ACTIVE_DISTRACTION"] += 0.8
        scores["FOCUSED_PRODUCTION"] = max(
            scores["FOCUSED_PRODUCTION"] - 0.5,
            0,
        )
    elif correction > 0 and interaction:
        # Natural revision is compatible with demanding composition.
        scores["FOCUSED_PRODUCTION"] += min(correction, 0.3)

    result = _normalize(scores)
    if previous:
        # Temporal persistence: one anomalous window cannot instantly relabel
        # the full episode.
        result = _normalize(
            {
                state: result[state] * 0.75 + previous[state] * 0.25
                for state in STATES
            }
        )
    return result


def assess_concentration(daily_data: dict[str, Any]) -> dict[str, Any]:
    """Return a temporal, probabilistic assessment dictated by the rubric."""

    windows = sorted(
        daily_data.get("attention_windows") or [],
        key=lambda item: str(item.get("timestamp") or ""),
    )
    totals = {state: 0.0 for state in STATES}
    total_weight = 0.0
    previous: dict[str, float] | None = None
    per_window = []
    goal_alignment_total = 0.0

    alignment = {
        "TASK_RELATED": 0.9,
        "COMMUNICATION": 0.5,
        "AMBIGUOUS": 0.5,
        "ENTERTAINMENT": 0.1,
    }
    for window in windows:
        probabilities = _window_probabilities(window, previous)
        previous = probabilities
        weight = max(float(window.get("duration_seconds") or 0), 1.0)
        for state in STATES:
            totals[state] += probabilities[state] * weight
        total_weight += weight
        goal_alignment_total += (
            alignment.get(str(window.get("content_state")), 0.5) * weight
        )
        per_window.append(
            {
                "timestamp": window.get("timestamp"),
                "duration_seconds": window.get("duration_seconds", 0),
                "probabilities": {
                    key: round(value, 4)
                    for key, value in probabilities.items()
                },
            }
        )

    probabilities = (
        {state: totals[state] / total_weight for state in STATES}
        if total_weight
        else {state: 1 / len(STATES) for state in STATES}
    )
    focus_probability = sum(
        probabilities[state]
        for state in (
            "FOCUSED_PRODUCTION",
            "FOCUSED_RECEPTION_REASONING",
            "FOCUSED_TASK_TRANSITION",
        )
    )
    dominant = max(probabilities, key=probabilities.get)
    sorted_values = sorted(probabilities.values(), reverse=True)
    confidence_margin = (
        sorted_values[0] - sorted_values[1]
        if len(sorted_values) > 1
        else 0.0
    )
    return {
        "framework": "context_aware_temporal_rubric_v1",
        "state_probabilities": {
            key: round(value, 4) for key, value in probabilities.items()
        },
        "concentration_probability": round(focus_probability, 4),
        "goal_alignment_probability": round(
            goal_alignment_total / total_weight if total_weight else 0.5,
            4,
        ),
        "dominant_state": dominant,
        "confidence": (
            "HIGH"
            if confidence_margin >= 0.25
            else "MODERATE"
            if confidence_margin >= 0.1
            else "LOW"
        ),
        "principles_applied": [
            "Physical interaction is evidence of engagement, not concentration by itself.",
            "Brief inactivity on relevant stable content may be reading or reasoning.",
            "Semantic continuity matters more than raw switch count.",
            "Related return transitions may support the same task goal.",
            "Automatic interface changes contribute almost no attention evidence.",
            "Cognitive concentration and goal alignment are reported separately.",
            "Temporal persistence prevents one anomalous window from relabeling an episode.",
        ],
        "limitations": [
            "No personal or task-specific baseline is available yet.",
            "Probabilities are heuristic estimates and are not empirically calibrated yet.",
            "Mental state cannot be uniquely identified from behavioral metadata.",
        ],
        "windows": per_window,
    }


def format_rubric_assessment(assessment: dict[str, Any]) -> str:
    labels = {
        "FOCUSED_PRODUCTION": "专注生产",
        "FOCUSED_RECEPTION_REASONING": "专注阅读/思考",
        "FOCUSED_TASK_TRANSITION": "专注任务切换",
        "ACTIVE_DISTRACTION": "主动分心",
        "PASSIVE_DISTRACTION": "被动分心",
        "AWAY": "离开",
    }
    probabilities = assessment["state_probabilities"]
    lines = [
        (
            f"综合专注概率估计："
            f"{assessment['concentration_probability'] * 100:.0f}%"
            f"（置信度：{assessment['confidence']}）"
        ),
        (
            "认知投入与目标一致性分开评估："
            f"目标一致性 {assessment['goal_alignment_probability'] * 100:.0f}%。"
        ),
        "状态概率："
        + "，".join(
            f"{labels[state]} {probabilities[state] * 100:.0f}%"
            for state in STATES
        )
        + "。",
        (
            "判定遵循上下文与时间序列：输入活动只说明交互投入；"
            "短暂无输入不直接判为分心；界面切换结合语义连续性、"
            "停留时间、返回结构和切换来源解释。"
        ),
    ]
    if assessment["limitations"]:
        lines.append(
            "不确定性说明：" + "；".join(
                "尚无个人/任务基线"
                if "baseline" in item
                else "概率尚未通过实证数据校准"
                if "calibrated" in item
                else "行为元数据不能唯一确定心理状态"
                for item in assessment["limitations"]
            ) + "。"
        )
    return "\n".join(lines)
