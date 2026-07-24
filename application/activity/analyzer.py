from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from urllib.parse import urlparse


class MouseState(str, Enum):
    ACTIVE = "ACTIVE"
    LOW_ACTIVITY = "LOW_ACTIVITY"
    NO_ACTIVITY = "NO_ACTIVITY"


class KeyboardState(str, Enum):
    ACTIVE_TYPING = "ACTIVE_TYPING"
    OCCASIONAL_TYPING = "OCCASIONAL_TYPING"
    NO_TYPING = "NO_TYPING"


class ContentState(str, Enum):
    TASK_RELATED = "TASK_RELATED"
    ENTERTAINMENT = "ENTERTAINMENT"
    COMMUNICATION = "COMMUNICATION"
    AMBIGUOUS = "AMBIGUOUS"


class SwitchingPattern(str, Enum):
    RELATED_SWITCHING = "RELATED_SWITCHING"
    UNRELATED_SWITCHING = "UNRELATED_SWITCHING"
    STABLE = "STABLE"
    UNKNOWN = "UNKNOWN"


class AttentionState(str, Enum):
    FOCUSED_ACTIVE = "FOCUSED_ACTIVE"
    FOCUSED_PASSIVE = "FOCUSED_PASSIVE"
    DISTRACTED = "DISTRACTED"
    POSSIBLY_ZONED_OUT = "POSSIBLY_ZONED_OUT"
    AWAY = "AWAY"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True)
class ActivityWindowAnalysis:
    mouse_state: MouseState
    keyboard_state: KeyboardState
    content_state: ContentState
    switching_pattern: SwitchingPattern
    attention_state: AttentionState


_TASK_APPS = {
    "code",
    "visual studio code",
    "xcode",
    "pycharm",
    "intellij idea",
    "terminal",
    "iterm2",
    "python",
}
_TASK_DOMAINS = {
    "github.com",
    "gitlab.com",
    "stackoverflow.com",
    "docs.python.org",
    "developer.mozilla.org",
    "platform.openai.com",
    "chatgpt.com",
}
_ENTERTAINMENT_DOMAINS = {
    "netflix.com",
    "disneyplus.com",
    "twitch.tv",
    "bilibili.com",
    "instagram.com",
    "tiktok.com",
}
_COMMUNICATION_APPS = {
    "mail",
    "messages",
    "slack",
    "microsoft teams",
    "wechat",
    "微信",
    "zoom",
}
_COMMUNICATION_DOMAINS = {
    "mail.google.com",
    "outlook.live.com",
    "slack.com",
    "teams.microsoft.com",
}
_YOUTUBE_TASK_TERMS = {
    "tutorial",
    "lecture",
    "course",
    "programming",
    "coding",
    "python",
    "javascript",
    "typescript",
    "software engineering",
    "documentation",
    "教程",
    "课程",
    "编程",
}
_YOUTUBE_ENTERTAINMENT_TERMS = {
    "gameplay",
    "gaming",
    "celebrity",
    "trailer",
    "music video",
    "funny",
    "reaction",
    "游戏",
    "明星",
    "娱乐",
}


def _duration_minutes(duration_seconds: float) -> float:
    return max(float(duration_seconds), 1.0) / 60.0


def _nonnegative(value: float | int) -> float:
    return max(float(value), 0.0)


def analyze_mouse(
    *,
    duration_seconds: float,
    mouse_move_count: int,
    mouse_distance: float,
    mouse_click_count: int,
    scrolling_count: int,
) -> MouseState:
    """Classify aggregate mouse metrics; no pointer positions are required."""

    moves = _nonnegative(mouse_move_count)
    distance = _nonnegative(mouse_distance)
    clicks = _nonnegative(mouse_click_count)
    scrolls = _nonnegative(scrolling_count)
    if moves == distance == clicks == scrolls == 0:
        return MouseState.NO_ACTIVITY

    minutes = _duration_minutes(duration_seconds)
    active = (
        clicks / minutes >= 2
        or scrolls / minutes >= 3
        or (moves / minutes >= 20 and distance / minutes >= 500)
    )
    return MouseState.ACTIVE if active else MouseState.LOW_ACTIVITY


def analyze_keyboard(
    *,
    duration_seconds: float,
    keypress_count: int,
    typing_burst_count: int,
    average_typing_speed: float,
    longest_no_typing_period: float,
) -> KeyboardState:
    """Classify counts and timing only; actual key values are never accepted."""

    keypresses = _nonnegative(keypress_count)
    bursts = _nonnegative(typing_burst_count)
    speed = _nonnegative(average_typing_speed)
    longest_pause = _nonnegative(longest_no_typing_period)
    if keypresses == 0 and bursts == 0:
        return KeyboardState.NO_TYPING

    minutes = _duration_minutes(duration_seconds)
    active = (
        keypresses / minutes >= 20
        or (speed >= 20 and bursts >= 1)
        or (bursts / minutes >= 2 and keypresses / minutes >= 10)
    )
    # A single short burst in an otherwise inactive window is occasional,
    # even if its instantaneous typing speed was high.
    if duration_seconds > 0 and longest_pause >= duration_seconds * 0.9:
        active = active and bursts >= 2
    return (
        KeyboardState.ACTIVE_TYPING
        if active
        else KeyboardState.OCCASIONAL_TYPING
    )


def _hostname(value: str) -> str:
    candidate = value.strip().casefold()
    if not candidate:
        return ""
    try:
        return (urlparse(f"//{candidate}").hostname or "").removeprefix("www.")
    except ValueError:
        return ""


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[\w+#.-]{2,}", value.casefold())
        if not token.isdigit()
    }


def _domain_matches(hostname: str, domains: set[str]) -> bool:
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in domains
    )


def analyze_interface(
    *,
    current_task: str,
    application_name: str,
    window_title: str,
    website_domain: str,
    page_title: str,
    seconds_on_interface: float,
    interface_switch_count: int,
    recent_interfaces: list[str] | tuple[str, ...],
) -> ContentState:
    """Classify visible interface metadata without reading page contents."""

    app = application_name.strip().casefold()
    host = _hostname(website_domain)
    title = f"{window_title} {page_title}".strip().casefold()

    if app in _COMMUNICATION_APPS or _domain_matches(
        host, _COMMUNICATION_DOMAINS
    ):
        return ContentState.COMMUNICATION

    if host in {"youtube.com", "m.youtube.com"}:
        if any(term in title for term in _YOUTUBE_TASK_TERMS):
            return ContentState.TASK_RELATED
        if any(term in title for term in _YOUTUBE_ENTERTAINMENT_TERMS):
            return ContentState.ENTERTAINMENT
        return ContentState.AMBIGUOUS

    if _domain_matches(host, _ENTERTAINMENT_DOMAINS):
        return ContentState.ENTERTAINMENT
    if app in _TASK_APPS or _domain_matches(host, _TASK_DOMAINS):
        return ContentState.TASK_RELATED

    task_tokens = _tokens(current_task)
    interface_tokens = _tokens(
        f"{application_name} {window_title} {website_domain} {page_title}"
    )
    if task_tokens and task_tokens.intersection(interface_tokens):
        return ContentState.TASK_RELATED
    if (
        seconds_on_interface < 5
        and interface_switch_count >= 3
        and recent_interfaces
    ):
        return ContentState.AMBIGUOUS
    return ContentState.AMBIGUOUS


def analyze_switching(
    *,
    current_task: str,
    interface_switch_count: int,
    recent_interfaces: list[str] | tuple[str, ...],
) -> SwitchingPattern:
    """Classify switching from metadata labels, domains, or window summaries."""

    if interface_switch_count <= 1:
        return SwitchingPattern.STABLE
    if not recent_interfaces:
        return SwitchingPattern.UNKNOWN

    task_tokens = _tokens(current_task)
    related_count = 0
    unrelated_count = 0
    for interface in recent_interfaces:
        normalized = interface.casefold()
        tokens = _tokens(normalized)
        if (
            task_tokens.intersection(tokens)
            or any(app in normalized for app in _TASK_APPS)
            or any(domain in normalized for domain in _TASK_DOMAINS)
        ):
            related_count += 1
        if (
            any(domain in normalized for domain in _ENTERTAINMENT_DOMAINS)
            or any(term in normalized for term in _YOUTUBE_ENTERTAINMENT_TERMS)
        ):
            unrelated_count += 1

    if unrelated_count:
        return SwitchingPattern.UNRELATED_SWITCHING
    if related_count >= max(2, len(recent_interfaces) // 2):
        return SwitchingPattern.RELATED_SWITCHING
    if interface_switch_count >= 3:
        return SwitchingPattern.UNRELATED_SWITCHING
    return SwitchingPattern.UNKNOWN


def analyze_attention(
    *,
    mouse_state: MouseState | str,
    keyboard_state: KeyboardState | str,
    content_state: ContentState | str,
    switching_pattern: SwitchingPattern | str,
    duration_seconds: float,
    presence_sensor: bool | None,
) -> AttentionState:
    mouse = MouseState(mouse_state)
    keyboard = KeyboardState(keyboard_state)
    content = ContentState(content_state)
    switching = SwitchingPattern(switching_pattern)
    no_interaction = (
        mouse is MouseState.NO_ACTIVITY
        and keyboard is KeyboardState.NO_TYPING
    )

    if presence_sensor is False:
        return AttentionState.AWAY if no_interaction else AttentionState.UNCERTAIN
    if content is ContentState.ENTERTAINMENT:
        return AttentionState.DISTRACTED
    if switching is SwitchingPattern.UNRELATED_SWITCHING:
        return AttentionState.DISTRACTED
    if content is ContentState.TASK_RELATED:
        if no_interaction and duration_seconds >= 300:
            return AttentionState.POSSIBLY_ZONED_OUT
        if (
            mouse is MouseState.ACTIVE
            or keyboard is KeyboardState.ACTIVE_TYPING
        ):
            return AttentionState.FOCUSED_ACTIVE
        return AttentionState.FOCUSED_PASSIVE
    if (
        switching is SwitchingPattern.RELATED_SWITCHING
        and content is not ContentState.ENTERTAINMENT
    ):
        return (
            AttentionState.FOCUSED_ACTIVE
            if not no_interaction
            else AttentionState.FOCUSED_PASSIVE
        )
    return AttentionState.UNCERTAIN


def analyze_activity_window(
    *,
    duration_seconds: float,
    mouse_move_count: int,
    mouse_distance: float,
    mouse_click_count: int,
    scrolling_count: int,
    keypress_count: int,
    typing_burst_count: int,
    average_typing_speed: float,
    longest_no_typing_period: float,
    current_task: str,
    application_name: str,
    window_title: str,
    website_domain: str,
    page_title: str,
    seconds_on_interface: float,
    interface_switch_count: int,
    recent_interfaces: list[str] | tuple[str, ...],
    presence_sensor: bool | None,
) -> ActivityWindowAnalysis:
    """Run the complete privacy-preserving attention pipeline."""

    mouse = analyze_mouse(
        duration_seconds=duration_seconds,
        mouse_move_count=mouse_move_count,
        mouse_distance=mouse_distance,
        mouse_click_count=mouse_click_count,
        scrolling_count=scrolling_count,
    )
    keyboard = analyze_keyboard(
        duration_seconds=duration_seconds,
        keypress_count=keypress_count,
        typing_burst_count=typing_burst_count,
        average_typing_speed=average_typing_speed,
        longest_no_typing_period=longest_no_typing_period,
    )
    content = analyze_interface(
        current_task=current_task,
        application_name=application_name,
        window_title=window_title,
        website_domain=website_domain,
        page_title=page_title,
        seconds_on_interface=seconds_on_interface,
        interface_switch_count=interface_switch_count,
        recent_interfaces=recent_interfaces,
    )
    switching = analyze_switching(
        current_task=current_task,
        interface_switch_count=interface_switch_count,
        recent_interfaces=recent_interfaces,
    )
    attention = analyze_attention(
        mouse_state=mouse,
        keyboard_state=keyboard,
        content_state=content,
        switching_pattern=switching,
        duration_seconds=duration_seconds,
        presence_sensor=presence_sensor,
    )
    return ActivityWindowAnalysis(
        mouse_state=mouse,
        keyboard_state=keyboard,
        content_state=content,
        switching_pattern=switching,
        attention_state=attention,
    )
