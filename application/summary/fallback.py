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
        blocks = daily_data.get("activity_blocks", [])
        if blocks:
            for block in blocks[-3:]:
                title = str(block.get("window_title") or "").strip()
                app = str(block.get("app") or "Unknown")
                subject = f"{app} — {title}" if title else app
                start = str(block.get("start") or "")[11:16]
                completed.append(f"{start}：{subject}")
        elif apps:
            names = [str(item.get("app", "Unknown")) for item in apps[:3]]
            completed.append(
                "观察到的前台应用：" + "、".join(names)
            )
        else:
            completed.append("今天没有足够的电脑上下文用于判断具体活动。")

        if apps:
            top = apps[0]
            insights.append(
                f"记录中停留时间最长的应用是 {top.get('app', 'Unknown')}，"
                f"约 {_duration(int(top.get('estimated_seconds', 0)))}。"
            )
        insights.append("前台窗口记录不能证明具体成果已经完成。")

        suggestions = ["从最后一个有标题的工作窗口继续。"]

        return DailySummary(
            headline=(
                f"今日工作集中在 {apps[0].get('app', 'Unknown')}"
                if apps
                else "今日缺少可识别的工作窗口"
            ),
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
                "当前为本地规则生成；具体活动仅依据前台应用"
                "和窗口采样估算。"
            ),
        )
