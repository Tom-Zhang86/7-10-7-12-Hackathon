"""Centralized AI Desk V2 configuration (Master Spec section 3).

Only the fields on ``OwnerHandoffConfig`` are configurable via environment
variables. Mandatory safety invariants — wearable UNKNOWN never triggers,
contradictory signals always wait, external actions denied in v1, OCR's
fixed Tier 1 -> Tier 2 -> OCR invocation order, Codex isolation fail-closed,
original workspace never writable, external publish/push/message/deploy
prohibited — are centralized as plain module constants below and are
deliberately NOT read from the environment: an override capability there
would defeat the exact property the constant exists to guarantee. See
docs/AI_DESK_V2_MASTER_SPEC.md, "Phase 1 Review Addendum," for the full,
approved list and rationale.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
import math
import os


class ConfigError(ValueError):
    """Raised when an environment override is present but invalid.

    Invalid overrides must fail loudly at startup rather than silently
    falling back to a default, so a typo in ``.env`` can never produce an
    unnoticed, unsafe configuration.
    """


# ---------------------------------------------------------------------------
# Mandatory safety invariants. These are NOT environment-overridable by
# design (Phase 1 review correction) — do not add an env var for any of
# these, and do not read them from ``os.environ`` anywhere in this package.
# ---------------------------------------------------------------------------
WEARABLE_UNKNOWN_NEVER_TRIGGERS: bool = True
CONTRADICTORY_SIGNALS_POLICY: str = "wait"
EXTERNAL_ACTIONS_ENABLED: bool = False
OCR_INVOCATION_ORDER: tuple[str, ...] = (
    "activity_classifier",
    "context_analyzer",
    "ocr",
)
# An unanswered/timed-out handoff question always resolves to D ("Do
# nothing"). This is fixed behavior, not a tunable default: there is
# deliberately no environment variable for "what happens on timeout,"
# because that is exactly the kind of switch that could someday be set to
# something unsafe.
UNANSWERED_QUESTION_DEFAULT: str = "D"
# A missing/unusable Codex CLI always stops safely. This is fixed behavior,
# not a policy string an environment variable could later flip to an unsafe
# fallback (e.g. "proceed without sandboxing"); there is deliberately no
# environment variable for it.
MISSING_CODEX_CLI_POLICY: str = "stop_safely"


_OCR_ADAPTER_MODES = frozenset({"noop", "mock"})

ENV_LEAVE_CONFIRM_SECONDS = "AI_DESK_V2_LEAVE_CONFIRM_SECONDS"
ENV_INPUT_IDLE_SECONDS = "AI_DESK_V2_INPUT_IDLE_SECONDS"
ENV_QUESTION_EXPIRY_SECONDS = "AI_DESK_V2_QUESTION_EXPIRY_SECONDS"
ENV_CODING_MAX_RUNTIME_SECONDS = "AI_DESK_V2_CODING_MAX_RUNTIME_SECONDS"
ENV_CODING_MAX_STEPS = "AI_DESK_V2_CODING_MAX_STEPS"
ENV_DB_PATH = "AI_DESK_V2_DB_PATH"
ENV_SESSION_ROOT = "AI_DESK_V2_SESSION_ROOT"
ENV_OCR_MODE = "AI_DESK_V2_OCR_MODE"
ENV_WEARABLE_DEVICE_IDS = "AI_DESK_V2_WEARABLE_DEVICE_IDS"


@dataclass(frozen=True)
class OwnerHandoffConfig:
    """Phase 1 configurable values (Master Spec section 3).

    Every field here may be overridden by an environment variable (see the
    ``ENV_*`` constants above); every override is validated by
    ``load_owner_handoff_config`` before use.
    """

    owner_leave_confirmation_seconds: float = 10.0
    input_idle_threshold_seconds: float = 5.0
    handoff_question_expiration_seconds: float = 60.0
    coding_agent_max_runtime_seconds: float = 300.0
    coding_agent_max_safe_steps: int = 20
    owner_handoff_db_path: Path = Path("data/owner_handoff.sqlite3")
    workspace_session_root: Path = Path("data/owner_handoff/sessions")
    ocr_adapter_mode: str = "noop"
    # Comma-separated allowlist of wearable device_ids. Empty by default,
    # which means every real/simulated wearable answer is rejected until
    # the caller explicitly configures at least one device id — there is no
    # "accept anything" fallback.
    wearable_device_allowlist: tuple[str, ...] = ()


def _positive_finite_float(raw: str, field_name: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{field_name} must be a number, got {raw!r}") from exc
    if not math.isfinite(value) or value <= 0:
        raise ConfigError(
            f"{field_name} must be a positive finite number, got {raw!r}"
        )
    return value


def _positive_int(raw: str, field_name: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{field_name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ConfigError(f"{field_name} must be a positive integer, got {raw!r}")
    return value


def _safe_path(raw: str, field_name: str) -> Path:
    stripped = raw.strip()
    if not stripped:
        raise ConfigError(f"{field_name} must not be empty")
    path = Path(stripped)
    # A root is its own parent on every platform (POSIX "/", Windows
    # "C:\\" or "\\") — this check is deliberately platform-independent.
    if path.parent == path:
        raise ConfigError(
            f"{field_name} must not be a filesystem root, got {raw!r}"
        )
    return path


def _parse_device_allowlist(raw: str) -> tuple[str, ...]:
    """Comma-separated device ids, whitespace-trimmed, duplicates removed
    deterministically (first occurrence wins, order preserved)."""

    seen: set[str] = set()
    ordered: list[str] = []
    for item in raw.split(","):
        trimmed = item.strip()
        if not trimmed or trimmed in seen:
            continue
        seen.add(trimmed)
        ordered.append(trimmed)
    return tuple(ordered)


def load_owner_handoff_config(
    env: Mapping[str, str] | None = None,
) -> OwnerHandoffConfig:
    """Load configuration once at startup.

    Pass ``env`` explicitly in tests instead of mutating ``os.environ`` so
    tests never depend on, or leak into, real process environment state.
    """
    source: Mapping[str, str] = env if env is not None else os.environ
    defaults = OwnerHandoffConfig()

    owner_leave_confirmation_seconds = (
        _positive_finite_float(source[ENV_LEAVE_CONFIRM_SECONDS], ENV_LEAVE_CONFIRM_SECONDS)
        if ENV_LEAVE_CONFIRM_SECONDS in source
        else defaults.owner_leave_confirmation_seconds
    )
    input_idle_threshold_seconds = (
        _positive_finite_float(source[ENV_INPUT_IDLE_SECONDS], ENV_INPUT_IDLE_SECONDS)
        if ENV_INPUT_IDLE_SECONDS in source
        else defaults.input_idle_threshold_seconds
    )
    handoff_question_expiration_seconds = (
        _positive_finite_float(source[ENV_QUESTION_EXPIRY_SECONDS], ENV_QUESTION_EXPIRY_SECONDS)
        if ENV_QUESTION_EXPIRY_SECONDS in source
        else defaults.handoff_question_expiration_seconds
    )
    coding_agent_max_runtime_seconds = (
        _positive_finite_float(
            source[ENV_CODING_MAX_RUNTIME_SECONDS], ENV_CODING_MAX_RUNTIME_SECONDS
        )
        if ENV_CODING_MAX_RUNTIME_SECONDS in source
        else defaults.coding_agent_max_runtime_seconds
    )
    coding_agent_max_safe_steps = (
        _positive_int(source[ENV_CODING_MAX_STEPS], ENV_CODING_MAX_STEPS)
        if ENV_CODING_MAX_STEPS in source
        else defaults.coding_agent_max_safe_steps
    )
    owner_handoff_db_path = (
        _safe_path(source[ENV_DB_PATH], ENV_DB_PATH)
        if ENV_DB_PATH in source
        else defaults.owner_handoff_db_path
    )
    workspace_session_root = (
        _safe_path(source[ENV_SESSION_ROOT], ENV_SESSION_ROOT)
        if ENV_SESSION_ROOT in source
        else defaults.workspace_session_root
    )
    ocr_adapter_mode = source.get(ENV_OCR_MODE, defaults.ocr_adapter_mode)
    if ocr_adapter_mode not in _OCR_ADAPTER_MODES:
        raise ConfigError(
            f"{ENV_OCR_MODE} must be one of {sorted(_OCR_ADAPTER_MODES)}, "
            f"got {ocr_adapter_mode!r}"
        )
    wearable_device_allowlist = (
        _parse_device_allowlist(source[ENV_WEARABLE_DEVICE_IDS])
        if ENV_WEARABLE_DEVICE_IDS in source
        else defaults.wearable_device_allowlist
    )

    return OwnerHandoffConfig(
        owner_leave_confirmation_seconds=owner_leave_confirmation_seconds,
        input_idle_threshold_seconds=input_idle_threshold_seconds,
        handoff_question_expiration_seconds=handoff_question_expiration_seconds,
        coding_agent_max_runtime_seconds=coding_agent_max_runtime_seconds,
        coding_agent_max_safe_steps=coding_agent_max_safe_steps,
        owner_handoff_db_path=owner_handoff_db_path,
        workspace_session_root=workspace_session_root,
        ocr_adapter_mode=ocr_adapter_mode,
        wearable_device_allowlist=wearable_device_allowlist,
    )
