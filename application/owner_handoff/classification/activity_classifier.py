"""Tier 1 deterministic activity classification (Master Spec section 4).

Tier 1 never invokes OCR and never inspects window titles, Chrome domains, or
filenames — only aggregate interaction counts and the active application
name. The app-name hint lists below are deliberately small and are used only
as bounded hints (to demote a known entertainment/communication surface, or
to confirm a known task surface) — they are never treated as an exhaustive
or "final truth" classification of every application, per Master Spec
section 4 ("do not encode a large brittle list of applications as final
truth"; "active app alone must not prove a specific project or completed
task").
"""
from __future__ import annotations

from application.owner_handoff.domain.activity import (
    ActivityClassification,
    ActivitySample,
)

# A sample with meaningful interaction needs at least this many aggregate
# input events (keypresses + mouse moves/clicks/scrolls) to be considered
# more than "low interaction" / "passive reading".
MEANINGFUL_INTERACTION_EVENTS = 3

# More interface switches than this within one sample window is treated as
# "rapid unexplained switching" and stays AMBIGUOUS even with interaction.
RAPID_SWITCH_THRESHOLD = 3

# Bounded hint list: surfaces with no user present (or where WORKING would
# never apply) that, combined with zero interaction, mean NOT_WORKING.
_LOCK_SURFACES = frozenset({"loginwindow", "screensaverengine"})

# Bounded hint list: a small set of task-oriented surfaces. Presence on this
# list is required (not merely sufficient) for a Tier 1 WORKING result —
# unknown applications remain AMBIGUOUS at Tier 1 regardless of interaction
# level, deferring to Tier 2/repeated context, per Master Spec section 4.
_TASK_SURFACE_HINTS = frozenset(
    {
        "terminal",
        "iterm2",
        "iterm",
        "code",
        "visual studio code",
        "xcode",
        "pycharm",
        "intellij idea",
    }
)

# Bounded hint list: known communication/entertainment surfaces stay
# AMBIGUOUS at Tier 1 even with high interaction (Master Spec section 4).
_ENTERTAINMENT_COMMUNICATION_HINTS = frozenset(
    {
        "netflix",
        "youtube",
        "twitch",
        "tiktok",
        "instagram",
        "messages",
        "slack",
        "discord",
        "wechat",
        "zoom",
        "mail",
    }
)


class ActivityClassifier:
    """Deterministic Tier 1 classifier over one ``ActivitySample``."""

    def classify(self, sample: ActivitySample) -> ActivityClassification:
        app_key = (sample.active_application or "").strip().lower()
        total_events = (
            sample.keypress_count
            + sample.mouse_move_count
            + sample.mouse_click_count
            + sample.scroll_count
        )
        has_interaction = total_events > 0
        no_active_interface = app_key == ""
        is_lock_surface = app_key in _LOCK_SURFACES

        if (no_active_interface or is_lock_surface) and not has_interaction:
            return ActivityClassification.NOT_WORKING

        if app_key in _ENTERTAINMENT_COMMUNICATION_HINTS:
            return ActivityClassification.AMBIGUOUS

        if (
            total_events >= MEANINGFUL_INTERACTION_EVENTS
            and app_key in _TASK_SURFACE_HINTS
            and sample.interface_switch_count <= RAPID_SWITCH_THRESHOLD
        ):
            return ActivityClassification.WORKING

        return ActivityClassification.AMBIGUOUS
