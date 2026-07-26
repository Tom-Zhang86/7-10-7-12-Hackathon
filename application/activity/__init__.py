"""Privacy-preserving interaction and attention analysis."""

from application.activity.collector import ActivityMetricsCollector
from application.activity.analyzer import (
    ActivityWindowAnalysis,
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

__all__ = [
    "ActivityWindowAnalysis",
    "ActivityMetricsCollector",
    "AttentionState",
    "ContentState",
    "KeyboardState",
    "MouseState",
    "SwitchingPattern",
    "analyze_attention",
    "analyze_activity_window",
    "analyze_interface",
    "analyze_keyboard",
    "analyze_mouse",
    "analyze_switching",
]
