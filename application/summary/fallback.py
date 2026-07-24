from typing import Any

from application.summary.models import DailySummary


def _duration(seconds: int) -> str:
    seconds = max(int(seconds), 0)
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours} 小时 {minutes} 分钟"
    if hours:
        return f"{hours} 小时"
    if minutes:
        return f"{minutes} 分钟"
    return f"{seconds} 秒"


class FallbackSummaryGenerator:
    """Produce a truthful local summary when the remote model is unavailable."""

    def generate(self, daily_data: dict[str, Any]) -> DailySummary:
        stats = daily_data.get("stats", {})
        work_seconds = int(stats.get("total_work_seconds", 0))
        longest = int(stats.get("longest_focus_seconds", 0))
        sessions = int(stats.get("session_count", 0))
        breaks = int(stats.get("break_count", 0))
        apps = daily_data.get("frequent_apps", [])

        completed = []
        insights = []
        if apps:
            names = [str(item.get("app", "Unknown")) for item in apps[:3]]
            completed.append(
                "主要前台活动涉及：" + "、".join(names)
            )
            for item in apps[:3]:
                insights.append(
                    f"{item.get('app', 'Unknown')} 的可估算前台时间约为"
                    f"{_duration(int(item.get('estimated_seconds', 0)))}。"
                )
        else:
            completed.append("今天没有足够的电脑上下文用于判断具体活动。")

        suggestions = ["继续记录工作上下文，以获得更准确的日报。"]
        if longest and longest < 25 * 60:
            suggestions.insert(0, "明天可尝试安排至少 25 分钟的连续专注时段。")
        elif longest:
            suggestions.insert(0, "延续今天较长的连续专注节奏。")

        return DailySummary(
            headline="今日工作概览",
            completed=completed,
            work_duration_summary=(
                f"今日累计工作 {_duration(work_seconds)}，"
                f"共 {sessions} 个 Session。"
            ),
            focus_assessment=(
                f"最长连续专注约 {_duration(longest)}，"
                f"记录到 {breaks} 次休息。"
            ),
            activity_insights=insights,
            tomorrow_suggestions=suggestions,
            data_quality_note=(
                "当前为本地规则生成的降级总结；具体活动仅依据前台应用"
                "和窗口采样估算。"
            ),
        )
