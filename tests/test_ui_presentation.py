import unittest
from datetime import date, datetime, timedelta, timezone

from application.summary.models import DailySummary, SummaryGeneration
from application.ui.presentation import (
    build_timeline_rows,
    format_duration,
    format_summary,
    present_status,
)
from models.state import PresenceState


class UIPresentationTest(unittest.TestCase):
    def test_formats_status_and_duration(self) -> None:
        self.assertEqual(present_status(PresenceState.WORKING).label, "工作中")
        self.assertEqual(present_status("Break").label, "休息")
        self.assertEqual(present_status("invalid").label, "未知")
        self.assertEqual(format_duration(3661), "01:01:01")
        self.assertEqual(format_duration(-5), "00:00:00")

    def test_builds_local_timeline_rows_and_truncates_titles(self) -> None:
        start = datetime(2026, 7, 9, 14, 0, tzinfo=timezone.utc)
        rows = build_timeline_rows(
            [
                {
                    "type": "session",
                    "timestamp": start,
                    "start_time": start,
                    "end_time": None,
                    "session_id": 1,
                    "duration_seconds": 0,
                },
                {
                    "type": "context_event",
                    "timestamp": start + timedelta(minutes=1),
                    "session_id": 1,
                    "payload": {
                        "app": "Code",
                        "window_title": "x" * 120,
                    },
                },
                {
                    "type": "break",
                    "timestamp": start + timedelta(minutes=2),
                    "start_time": start + timedelta(minutes=2),
                    "end_time": start + timedelta(minutes=7),
                    "session_id": 1,
                    "duration_seconds": 300,
                },
            ],
            local_timezone=timezone.utc,
        )

        self.assertEqual(rows[0].time, "14:00")
        self.assertEqual(rows[0].detail, "工作开始")
        self.assertEqual(rows[1].category, "活动")
        self.assertLessEqual(len(rows[1].detail), 100)
        self.assertEqual(rows[2].detail, "休息 · 00:05:00")

    def test_formats_summary_as_plain_document_not_chat(self) -> None:
        generation = SummaryGeneration(
            target_date=date(2026, 7, 9),
            generated_at=datetime(
                2026,
                7,
                9,
                20,
                0,
                tzinfo=timezone.utc,
            ),
            source="test",
            summary=DailySummary(
                headline="今日工作概览",
                completed=["实现了时间线展示"],
                work_duration_summary="今日工作 1 小时。",
                focus_assessment="最长专注 30 分钟。",
                activity_insights=["活动主要集中在 Code。"],
                tomorrow_suggestions=["继续完成 UI 联调。"],
                data_quality_note="内容依据前台窗口推断。",
            ),
        )

        text = format_summary(generation)

        self.assertIn("工作轨迹\n• 实现了时间线展示", text)
        self.assertIn("证据边界\n• 活动主要集中在 Code", text)
        self.assertIn("下一步\n• 继续完成 UI 联调", text)
        self.assertNotIn("Assistant:", text)
        self.assertNotIn("🤖", text)


if __name__ == "__main__":
    unittest.main()
