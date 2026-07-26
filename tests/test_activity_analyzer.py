import unittest

from application.activity import (
    AttentionState,
    ContentState,
    KeyboardState,
    MouseState,
    SwitchingPattern,
    analyze_attention,
    analyze_activity_window,
    analyze_interface,
    analyze_keyboard,
    analyze_mouse,
    analyze_switching,
)


class InteractionAnalyzerTest(unittest.TestCase):
    def test_mouse_states(self) -> None:
        self.assertEqual(
            analyze_mouse(
                duration_seconds=60,
                mouse_move_count=0,
                mouse_distance=0,
                mouse_click_count=0,
                scrolling_count=0,
            ),
            MouseState.NO_ACTIVITY,
        )
        self.assertEqual(
            analyze_mouse(
                duration_seconds=60,
                mouse_move_count=50,
                mouse_distance=2000,
                mouse_click_count=4,
                scrolling_count=2,
            ),
            MouseState.ACTIVE,
        )
        self.assertEqual(
            analyze_mouse(
                duration_seconds=300,
                mouse_move_count=2,
                mouse_distance=30,
                mouse_click_count=1,
                scrolling_count=0,
            ),
            MouseState.LOW_ACTIVITY,
        )

    def test_keyboard_uses_only_aggregate_metrics(self) -> None:
        self.assertEqual(
            analyze_keyboard(
                duration_seconds=60,
                keypress_count=0,
                typing_burst_count=0,
                average_typing_speed=0,
                longest_no_typing_period=60,
            ),
            KeyboardState.NO_TYPING,
        )
        self.assertEqual(
            analyze_keyboard(
                duration_seconds=60,
                keypress_count=45,
                typing_burst_count=3,
                average_typing_speed=38,
                longest_no_typing_period=10,
            ),
            KeyboardState.ACTIVE_TYPING,
        )


class InterfaceAnalyzerTest(unittest.TestCase):
    def test_task_and_entertainment_youtube_titles(self) -> None:
        common = {
            "current_task": "Learn Python",
            "application_name": "Google Chrome",
            "window_title": "",
            "website_domain": "youtube.com",
            "seconds_on_interface": 120,
            "interface_switch_count": 1,
            "recent_interfaces": [],
        }
        self.assertEqual(
            analyze_interface(
                **common,
                page_title="Python async programming tutorial",
            ),
            ContentState.TASK_RELATED,
        )
        self.assertEqual(
            analyze_interface(
                **common,
                page_title="Celebrity reaction and funny gaming clips",
            ),
            ContentState.ENTERTAINMENT,
        )

    def test_communication_and_task_tools(self) -> None:
        values = {
            "current_task": "Implement API support",
            "window_title": "",
            "website_domain": "",
            "page_title": "",
            "seconds_on_interface": 30,
            "interface_switch_count": 0,
            "recent_interfaces": [],
        }
        self.assertEqual(
            analyze_interface(application_name="Slack", **values),
            ContentState.COMMUNICATION,
        )
        self.assertEqual(
            analyze_interface(application_name="Terminal", **values),
            ContentState.TASK_RELATED,
        )


class AttentionAnalyzerTest(unittest.TestCase):
    def test_focused_active_and_passive(self) -> None:
        base = {
            "content_state": ContentState.TASK_RELATED,
            "switching_pattern": SwitchingPattern.STABLE,
            "presence_sensor": True,
        }
        self.assertEqual(
            analyze_attention(
                **base,
                mouse_state=MouseState.ACTIVE,
                keyboard_state=KeyboardState.NO_TYPING,
                duration_seconds=60,
            ),
            AttentionState.FOCUSED_ACTIVE,
        )
        self.assertEqual(
            analyze_attention(
                **base,
                mouse_state=MouseState.LOW_ACTIVITY,
                keyboard_state=KeyboardState.NO_TYPING,
                duration_seconds=120,
            ),
            AttentionState.FOCUSED_PASSIVE,
        )

    def test_zoned_out_requires_five_minutes_and_task_context(self) -> None:
        base = {
            "mouse_state": MouseState.NO_ACTIVITY,
            "keyboard_state": KeyboardState.NO_TYPING,
            "content_state": ContentState.TASK_RELATED,
            "switching_pattern": SwitchingPattern.STABLE,
            "presence_sensor": True,
        }
        self.assertEqual(
            analyze_attention(**base, duration_seconds=299),
            AttentionState.FOCUSED_PASSIVE,
        )
        self.assertEqual(
            analyze_attention(**base, duration_seconds=300),
            AttentionState.POSSIBLY_ZONED_OUT,
        )

    def test_absence_and_distraction_precedence(self) -> None:
        self.assertEqual(
            analyze_attention(
                mouse_state=MouseState.NO_ACTIVITY,
                keyboard_state=KeyboardState.NO_TYPING,
                content_state=ContentState.AMBIGUOUS,
                switching_pattern=SwitchingPattern.UNKNOWN,
                duration_seconds=30,
                presence_sensor=False,
            ),
            AttentionState.AWAY,
        )
        self.assertEqual(
            analyze_attention(
                mouse_state=MouseState.ACTIVE,
                keyboard_state=KeyboardState.NO_TYPING,
                content_state=ContentState.ENTERTAINMENT,
                switching_pattern=SwitchingPattern.STABLE,
                duration_seconds=30,
                presence_sensor=True,
            ),
            AttentionState.DISTRACTED,
        )

    def test_related_and_unrelated_switching(self) -> None:
        self.assertEqual(
            analyze_switching(
                current_task="Build Python API",
                interface_switch_count=4,
                recent_interfaces=[
                    "Visual Studio Code main.py",
                    "Terminal pytest",
                    "docs.python.org",
                ],
            ),
            SwitchingPattern.RELATED_SWITCHING,
        )
        self.assertEqual(
            analyze_switching(
                current_task="Build Python API",
                interface_switch_count=4,
                recent_interfaces=[
                    "Visual Studio Code",
                    "netflix.com",
                    "gaming video",
                ],
            ),
            SwitchingPattern.UNRELATED_SWITCHING,
        )

    def test_complete_pipeline_accepts_every_requested_input(self) -> None:
        result = analyze_activity_window(
            duration_seconds=60,
            mouse_move_count=30,
            mouse_distance=900,
            mouse_click_count=3,
            scrolling_count=1,
            keypress_count=40,
            typing_burst_count=3,
            average_typing_speed=35,
            longest_no_typing_period=15,
            current_task="Implement Python API",
            application_name="Visual Studio Code",
            window_title="analyzer.py",
            website_domain="",
            page_title="",
            seconds_on_interface=60,
            interface_switch_count=3,
            recent_interfaces=["Visual Studio Code", "Terminal", "GitHub"],
            presence_sensor=True,
        )

        self.assertEqual(result.mouse_state, MouseState.ACTIVE)
        self.assertEqual(result.keyboard_state, KeyboardState.ACTIVE_TYPING)
        self.assertEqual(result.content_state, ContentState.TASK_RELATED)
        self.assertEqual(
            result.switching_pattern,
            SwitchingPattern.RELATED_SWITCHING,
        )
        self.assertEqual(result.attention_state, AttentionState.FOCUSED_ACTIVE)


if __name__ == "__main__":
    unittest.main()
