from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
import logging
from typing import Any
from urllib.parse import urlparse

from application.summary.fallback import FallbackSummaryGenerator
from application.summary.models import DailySummary, SummaryGeneration
from application.summary.rubric import (
    assess_concentration,
    format_rubric_assessment,
)
from utils.time_utils import utc_now

logger = logging.getLogger(__name__)


def _interface_label(value: Any) -> str:
    """Turn a stored interface signature into a short user-facing label."""

    parts = [part.strip() for part in str(value or "").split(" | ") if part.strip()]
    if not parts:
        return "未知界面"
    app = parts[0]
    detail = parts[1] if len(parts) > 1 else ""
    if len(parts) > 2:
        try:
            host = (urlparse(parts[2]).hostname or "").removeprefix("www.")
        except ValueError:
            host = ""
        detail = detail or host
    if detail and detail.casefold() != app.casefold():
        return f"{detail}（{app}）"
    return app


def _state_counts(
    windows: list[dict[str, Any]],
    field: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for window in windows:
        state = str(window.get(field) or "UNKNOWN")
        counts[state] = counts.get(state, 0) + 1
    return counts


def _average_metric(
    windows: list[dict[str, Any]],
    field: str,
) -> float | None:
    values = [
        float(window[field])
        for window in windows
        if window.get(field) is not None
    ]
    return sum(values) / len(values) if values else None


def _statistics_sections(windows: list[dict[str, Any]]) -> str:
    def average(field: str) -> float:
        return _average_metric(windows, field) or 0.0

    def total(field: str) -> int:
        return sum(int(window.get(field) or 0) for window in windows)

    def maximum(field: str) -> float:
        return max(
            (
                float(window.get(field) or 0)
                for window in windows
            ),
            default=0.0,
        )

    mouse_states = _state_counts(windows, "mouse_state")
    keyboard_states = _state_counts(windows, "keyboard_state")
    content_states = _state_counts(windows, "content_state")
    switching_states = _state_counts(windows, "switching_pattern")

    pause_totals = {
        "under_1s": 0,
        "1_to_3s": 0,
        "3_to_10s": 0,
        "10s_or_more": 0,
    }
    origin_totals = {
        "LIKELY_USER_INITIATED": 0,
        "LIKELY_AUTOMATIC": 0,
        "UNKNOWN": 0,
    }
    return_patterns: list[str] = []
    recent_interfaces: list[str] = []
    for window in windows:
        for bucket, count in (
            window.get("typing_pause_distribution") or {}
        ).items():
            if bucket in pause_totals:
                pause_totals[bucket] += int(count or 0)
        for origin, count in (
            window.get("interface_change_origin_counts") or {}
        ).items():
            if origin in origin_totals:
                origin_totals[origin] += int(count or 0)
        for pattern in window.get("interface_return_patterns") or []:
            label = str(pattern)
            if label not in return_patterns:
                return_patterns.append(label)
        for interface in window.get("recent_interfaces") or []:
            label = _interface_label(interface)
            if label not in recent_interfaces:
                recent_interfaces.append(label)

    detail_windows = sum(
        bool(window.get("keyboard_detail_available"))
        for window in windows
    )
    lines = [
        "鼠标统计",
        (
            "状态窗口："
            f"活跃 {mouse_states.get('ACTIVE', 0)}，"
            f"低活动 {mouse_states.get('LOW_ACTIVITY', 0)}，"
            f"无活动 {mouse_states.get('NO_ACTIVITY', 0)}"
        ),
        f"总移动事件：{total('mouse_move_count')}",
        f"总移动距离：{sum(float(w.get('mouse_distance') or 0) for w in windows):.1f} 像素",
        f"总点击：{total('mouse_click_count')}",
        f"总滚动：{total('scrolling_count')}",
        f"平均活跃占比：{average('mouse_active_ratio') * 100:.1f}%",
        f"平均移动率：{average('mouse_movement_rate_per_minute'):.1f} 次/分钟",
        f"平均点击率：{average('mouse_click_rate_per_minute'):.1f} 次/分钟",
        f"平均滚动率：{average('mouse_scroll_rate_per_minute'):.1f} 次/分钟",
        f"空闲间隔数：{total('mouse_idle_gap_count')}",
        f"平均空闲间隔：{average('mouse_average_idle_gap_seconds'):.2f} 秒",
        f"最长空闲间隔：{maximum('mouse_longest_idle_gap_seconds'):.2f} 秒",
        f"平均速度：{average('mouse_average_velocity_pixels_per_second'):.2f} 像素/秒",
        f"平均轨迹效率：{average('mouse_trajectory_efficiency'):.3f}",
        f"平均方向熵：{average('mouse_directional_entropy'):.3f}",
        "",
        "键盘统计",
        (
            "状态窗口："
            f"持续输入 {keyboard_states.get('ACTIVE_TYPING', 0)}，"
            f"偶尔输入 {keyboard_states.get('OCCASIONAL_TYPING', 0)}，"
            f"无输入 {keyboard_states.get('NO_TYPING', 0)}"
        ),
        f"详细键盘窗口：{detail_windows}/{len(windows)}",
        f"总按键数：{total('keypress_count')}",
        f"平均按键率：{average('keystrokes_per_minute'):.1f} 次/分钟",
        f"平均输入速度：{average('average_typing_speed'):.1f} 次/分钟",
        f"输入段数：{total('typing_burst_count')}",
        f"平均输入段长度：{average('average_typing_burst_length'):.2f} 次按键",
        f"最长输入段长度：{maximum('longest_typing_burst_length'):.0f} 次按键",
        f"平均按键间隔：{average('average_inter_key_interval_seconds'):.3f} 秒",
        f"中位按键间隔：{average('median_inter_key_interval_seconds'):.3f} 秒",
        f"最长无输入时间：{maximum('longest_no_typing_period'):.2f} 秒",
        (
            "停顿分布："
            f"<1秒 {pause_totals['under_1s']}，"
            f"1–3秒 {pause_totals['1_to_3s']}，"
            f"3–10秒 {pause_totals['3_to_10s']}，"
            f"≥10秒 {pause_totals['10s_or_more']}"
        ),
        f"修正：{total('correction_count')} 次，平均率 {average('correction_rate') * 100:.2f}%",
        f"快捷键：{total('shortcut_count')} 次，平均率 {average('shortcut_rate') * 100:.2f}%",
        f"重复：{total('repetition_count')} 次，平均率 {average('repetition_rate') * 100:.2f}%",
        "",
        "界面统计",
        (
            "内容窗口："
            f"任务相关 {content_states.get('TASK_RELATED', 0)}，"
            f"娱乐 {content_states.get('ENTERTAINMENT', 0)}，"
            f"沟通 {content_states.get('COMMUNICATION', 0)}，"
            f"模糊 {content_states.get('AMBIGUOUS', 0)}"
        ),
        (
            "切换窗口："
            f"相关 {switching_states.get('RELATED_SWITCHING', 0)}，"
            f"不相关 {switching_states.get('UNRELATED_SWITCHING', 0)}，"
            f"稳定 {switching_states.get('STABLE', 0)}，"
            f"未知 {switching_states.get('UNKNOWN', 0)}"
        ),
        f"总切换次数：{total('interface_switch_count')}",
        f"平均切换频率：{average('interface_switch_frequency_per_minute'):.2f} 次/分钟",
        f"平均语义相似度：{average('interface_average_semantic_similarity'):.3f}",
        f"平均停留时间：{average('interface_average_dwell_seconds'):.2f} 秒",
        f"当前界面平均持续：{average('seconds_on_interface'):.2f} 秒",
        f"平均停留变异系数：{average('interface_dwell_time_cv'):.3f}",
        f"平均停留稳定度：{average('interface_dwell_stability'):.3f}",
        f"平均短停留比例：{average('interface_short_dwell_ratio') * 100:.1f}%",
        f"返回旧界面：{total('interface_return_count')} 次",
        f"平均返回率：{average('interface_return_rate') * 100:.1f}%",
        (
            "切换来源："
            f"用户触发 {origin_totals['LIKELY_USER_INITIATED']}，"
            f"自动变化 {origin_totals['LIKELY_AUTOMATIC']}，"
            f"未知 {origin_totals['UNKNOWN']}"
        ),
        "返回模式：" + ("；".join(return_patterns[-3:]) or "无"),
        "最近界面：" + ("、".join(recent_interfaces[-5:]) or "无"),
    ]
    return "\n".join(lines)


class ManualSummaryService:
    """Generate a summary only when explicitly called by the UI/user."""

    def __init__(
        self,
        aggregator: Any,
        llm_client: Any,
        store: Any,
        fallback: FallbackSummaryGenerator | None = None,
        retry_count: int = 1,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.aggregator = aggregator
        self.llm_client = llm_client
        self.store = store
        self.fallback = fallback or FallbackSummaryGenerator()
        self.retry_count = max(retry_count, 0)
        self.clock = clock
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="daily-summary",
        )

    def generate_today(self) -> SummaryGeneration:
        """Perform one explicit generation request and persist its result."""

        daily_data = self.aggregator.build_today()
        daily_data["concentration_rubric_assessment"] = (
            assess_concentration(daily_data)
        )
        generated_at = self.clock()
        summary: DailySummary | None = None
        last_error: Exception | None = None

        for _attempt in range(self.retry_count + 1):
            try:
                summary = DailySummary.from_dict(
                    self.llm_client.generate(daily_data)
                )
                break
            except Exception as exc:
                last_error = exc
                logger.warning("Remote daily summary failed: %s", exc)

        if summary is None:
            summary = self.fallback.generate(daily_data)
            source = "fallback"
            warning = str(last_error) if last_error else "Unknown LLM error."
        else:
            source = getattr(self.llm_client, "source_name", "llm")
            warning = None
        ai_reason = summary.focus_assessment if source != "fallback" else ""
        summary = self._user_facing_metrics(daily_data, ai_reason)

        generation = SummaryGeneration(
            target_date=generated_at.date(),
            generated_at=generated_at,
            source=source,
            summary=summary,
            warning=warning,
        )
        self.store.save(generation)
        return generation

    @staticmethod
    def _user_facing_metrics(
        daily_data: dict[str, Any],
        ai_reason: str = "",
    ) -> DailySummary:
        """Show one decisive outcome while keeping rubric evidence internal."""

        metrics = daily_data.get("behavior_metrics") or {}
        assessment = (
            daily_data.get("concentration_rubric_assessment")
            or assess_concentration(daily_data)
        )

        def duration(value: Any) -> str:
            total = max(int(value or 0), 0)
            minutes, seconds = divmod(total, 60)
            if minutes and seconds:
                return f"{minutes} 分 {seconds} 秒"
            if minutes:
                return f"{minutes} 分钟"
            return f"{seconds} 秒"

        labels = {
            "FOCUSED_PRODUCTION": "专注生产",
            "FOCUSED_RECEPTION_REASONING": "专注阅读与思考",
            "FOCUSED_TASK_TRANSITION": "专注任务切换",
            "ACTIVE_DISTRACTION": "主动分心",
            "PASSIVE_DISTRACTION": "被动分心",
            "AWAY": "离开电脑",
        }
        dominant = labels.get(
            str(assessment.get("dominant_state")),
            "专注生产",
        )
        focus_rate = max(
            min(int(metrics.get("focus_rate_percent") or 0), 100),
            0,
        )
        statistics_text = _statistics_sections(
            daily_data.get("attention_windows") or []
        )
        return DailySummary(
            headline="今日专注表现",
            completed=[],
            work_duration_summary="",
            focus_assessment=(
                f"AI 判断：今天的主要状态是{dominant}。\n\n"
                f"已分类有效专注："
                f"{duration(metrics.get('effective_focus_seconds'))}\n"
                f"已分类时间专注率：{focus_rate}%\n"
                f"平均专注："
                f"{duration(metrics.get('average_concentration_seconds'))}\n"
                f"最长专注："
                f"{duration(metrics.get('longest_concentration_seconds'))}\n"
                f"分心次数："
                f"{max(int(metrics.get('distraction_period_count') or 0), 0)} 次\n"
                f"平均恢复时间："
                f"{duration(metrics.get('average_recovery_seconds'))}"
                f"\n\n{statistics_text}"
            ),
            activity_insights=[],
            tomorrow_suggestions=[],
            data_quality_note="",
        )

    @staticmethod
    def _detailed_user_facing_metrics(
        daily_data: dict[str, Any],
        ai_reason: str = "",
    ) -> DailySummary:
        """Expose metrics together with the recorded evidence behind them."""

        metrics = daily_data.get("behavior_metrics") or {}
        attention = daily_data.get("attention_summary") or {}
        attention_seconds = attention.get("attention_seconds") or {}
        windows = daily_data.get("attention_windows") or []

        def duration(value: Any) -> str:
            total = max(int(value or 0), 0)
            minutes, seconds = divmod(total, 60)
            if minutes and seconds:
                return f"{minutes} 分 {seconds} 秒"
            if minutes:
                return f"{minutes} 分钟"
            return f"{seconds} 秒"

        concentration = duration(
            metrics.get("average_concentration_seconds")
        )
        effective = duration(metrics.get("effective_focus_seconds"))
        longest = duration(
            metrics.get("longest_concentration_seconds")
        )
        focus_rate = max(min(int(metrics.get("focus_rate_percent") or 0), 100), 0)
        distraction_count = max(
            int(metrics.get("distraction_period_count") or 0),
            0,
        )
        recovery = duration(metrics.get("average_recovery_seconds"))
        focused_active = duration(attention_seconds.get("FOCUSED_ACTIVE"))
        focused_passive = duration(attention_seconds.get("FOCUSED_PASSIVE"))
        distracted = duration(attention_seconds.get("DISTRACTED"))
        uncertain_seconds = sum(
            max(float(seconds or 0), 0)
            for state, seconds in attention_seconds.items()
            if state not in {
                "FOCUSED_ACTIVE",
                "FOCUSED_PASSIVE",
                "DISTRACTED",
            }
        )

        rubric_assessment = (
            daily_data.get("concentration_rubric_assessment")
            or assess_concentration(daily_data)
        )
        reason_lines = [
            "结果原因：",
            "分析框架：上下文感知的时间序列量表。",
            format_rubric_assessment(rubric_assessment),
        ]
        if uncertain_seconds:
            reason_lines.append(
                f"另有 {duration(uncertain_seconds)} 无法确定，"
                "未计入专注率。"
            )
        if not attention.get("window_count"):
            reason_lines.extend(
                [
                "今天尚无可用的活动分类窗口，因此各项专注指标为 0。",
                ]
            )
        else:
            mouse = _state_counts(windows, "mouse_state")
            keyboard = _state_counts(windows, "keyboard_state")
            switching = _state_counts(windows, "switching_pattern")
            reason_lines.extend(
                [
                    (
                        "鼠标："
                        f"活跃 {mouse.get('ACTIVE', 0)} 个窗口，"
                        f"低活动 {mouse.get('LOW_ACTIVITY', 0)} 个窗口，"
                        f"无活动 {mouse.get('NO_ACTIVITY', 0)} 个窗口。"
                    ),
                    (
                        "键盘："
                        f"持续输入 {keyboard.get('ACTIVE_TYPING', 0)} 个窗口，"
                        f"偶尔输入 {keyboard.get('OCCASIONAL_TYPING', 0)} 个窗口，"
                        f"无输入 {keyboard.get('NO_TYPING', 0)} 个窗口。"
                    ),
                    (
                        "界面切换："
                        f"相关切换 {switching.get('RELATED_SWITCHING', 0)} 个窗口，"
                        f"不相关切换 {switching.get('UNRELATED_SWITCHING', 0)} 个窗口，"
                        f"稳定 {switching.get('STABLE', 0)} 个窗口。"
                    ),
                ]
            )
            switch_frequency = _average_metric(
                windows,
                "interface_switch_frequency_per_minute",
            )
            semantic_similarity = _average_metric(
                windows,
                "interface_average_semantic_similarity",
            )
            average_dwell = _average_metric(
                windows,
                "interface_average_dwell_seconds",
            )
            dwell_stability = _average_metric(
                windows,
                "interface_dwell_stability",
            )
            return_count = sum(
                int(window.get("interface_return_count") or 0)
                for window in windows
            )
            origin_totals = {
                "LIKELY_USER_INITIATED": 0,
                "LIKELY_AUTOMATIC": 0,
                "UNKNOWN": 0,
            }
            for window in windows:
                for origin, count in (
                    window.get("interface_change_origin_counts") or {}
                ).items():
                    if origin in origin_totals:
                        origin_totals[origin] += int(count or 0)
            if switch_frequency is not None:
                reason_lines.append(
                    "界面细节："
                    f"平均切换频率 {switch_frequency:.1f} 次/分钟，"
                    f"连续界面语义相似度 {semantic_similarity or 0:.2f}，"
                    f"平均停留 {average_dwell or 0:.1f} 秒，"
                    f"停留稳定度 {dwell_stability or 0:.2f}，"
                    f"返回旧界面 {return_count} 次。"
                )
                reason_lines.append(
                    "切换来源（推断）："
                    f"可能由用户触发 {origin_totals['LIKELY_USER_INITIATED']} 次，"
                    f"可能为自动变化 {origin_totals['LIKELY_AUTOMATIC']} 次，"
                    f"无法确定 {origin_totals['UNKNOWN']} 次。"
                )
            kpm = _average_metric(windows, "keystrokes_per_minute")
            burst_length = _average_metric(
                windows,
                "average_typing_burst_length",
            )
            inter_key = _average_metric(
                windows,
                "average_inter_key_interval_seconds",
            )
            correction_rate = _average_metric(windows, "correction_rate")
            shortcut_rate = _average_metric(windows, "shortcut_rate")
            repetition_rate = _average_metric(windows, "repetition_rate")
            pause_totals: dict[str, int] = {}
            for window in windows:
                for bucket, count in (
                    window.get("typing_pause_distribution") or {}
                ).items():
                    pause_totals[str(bucket)] = (
                        pause_totals.get(str(bucket), 0) + int(count or 0)
                    )
            if kpm is not None:
                reason_lines.append(
                    "键盘细节："
                    f"{kpm:.1f} 次/分钟，"
                    f"平均每段连续输入 {burst_length or 0:.1f} 次按键，"
                    f"平均按键间隔 {inter_key or 0:.2f} 秒；"
                    f"修正率 {(correction_rate or 0) * 100:.1f}%，"
                    f"快捷键率 {(shortcut_rate or 0) * 100:.1f}%，"
                    f"重复率 {(repetition_rate or 0) * 100:.1f}%。"
                )
                if pause_totals:
                    reason_lines.append(
                        "输入停顿分布："
                        f"<1秒 {pause_totals.get('under_1s', 0)} 次，"
                        f"1–3秒 {pause_totals.get('1_to_3s', 0)} 次，"
                        f"3–10秒 {pause_totals.get('3_to_10s', 0)} 次，"
                        f"≥10秒 {pause_totals.get('10s_or_more', 0)} 次。"
                    )
            active_ratio = _average_metric(windows, "mouse_active_ratio")
            movement_rate = _average_metric(
                windows,
                "mouse_movement_rate_per_minute",
            )
            click_rate = _average_metric(
                windows,
                "mouse_click_rate_per_minute",
            )
            scroll_rate = _average_metric(
                windows,
                "mouse_scroll_rate_per_minute",
            )
            velocity = _average_metric(
                windows,
                "mouse_average_velocity_pixels_per_second",
            )
            efficiency = _average_metric(
                windows,
                "mouse_trajectory_efficiency",
            )
            entropy = _average_metric(
                windows,
                "mouse_directional_entropy",
            )
            longest_idle_values = [
                float(window["mouse_longest_idle_gap_seconds"])
                for window in windows
                if window.get("mouse_longest_idle_gap_seconds") is not None
            ]
            if active_ratio is not None:
                reason_lines.append(
                    "鼠标细节："
                    f"平均活跃占比 {active_ratio * 100:.0f}%，"
                    f"移动 {movement_rate or 0:.1f} 次/分钟，"
                    f"点击 {click_rate or 0:.1f} 次/分钟，"
                    f"滚动 {scroll_rate or 0:.1f} 次/分钟；"
                    f"最长空闲间隔 {max(longest_idle_values, default=0):.1f} 秒，"
                    f"平均速度 {velocity or 0:.1f} 像素/秒，"
                    f"轨迹效率 {efficiency or 0:.2f}，"
                    f"方向熵 {entropy or 0:.2f}。"
                )

            example: tuple[str, str] | None = None
            for window in reversed(windows):
                if window.get("switching_pattern") != "UNRELATED_SWITCHING":
                    continue
                interfaces = []
                for item in window.get("recent_interfaces") or []:
                    label = _interface_label(item)
                    if not interfaces or interfaces[-1] != label:
                        interfaces.append(label)
                if len(interfaces) >= 2:
                    example = (interfaces[-2], interfaces[-1])
                    break
            if example:
                reason_lines.append(
                    f"例如：从{example[0]}切换到{example[1]}，"
                    "两者未显示共同任务线索，因此该窗口判定为分心。"
                )
            reason_lines.append(
                "综合以上鼠标、键盘和界面切换信号，"
                f"得到主动专注 {focused_active}、被动专注 "
                f"{focused_passive}、分心 {distracted}；"
                "专注率只使用已分类的专注与分心时长计算。"
            )
        if ai_reason.strip():
            reason_lines.extend(
                [
                    "",
                    "AI 量表分析：",
                    ai_reason.strip()[:1200],
                ]
            )

        return DailySummary(
            headline="今日专注表现（量表分析）",
            completed=[],
            work_duration_summary="",
            focus_assessment=(
                f"已分类有效专注：{effective}\n"
                f"已分类时间专注率：{focus_rate}%\n"
                f"平均专注：{concentration}\n"
                f"最长专注：{longest}\n"
                f"分心次数：{distraction_count} 次\n"
                f"平均恢复时间：{recovery}\n\n"
                + "\n".join(reason_lines)
            ),
            activity_insights=[],
            tomorrow_suggestions=[],
            data_quality_note="",
        )

    def generate_today_async(self) -> Future[SummaryGeneration]:
        """Run a manual request off the Runtime and future UI threads."""

        return self._executor.submit(self.generate_today)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
