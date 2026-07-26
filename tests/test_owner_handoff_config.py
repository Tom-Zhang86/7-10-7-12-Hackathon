import unittest
from pathlib import Path

from application.owner_handoff.config import (
    CONTRADICTORY_SIGNALS_POLICY,
    EXTERNAL_ACTIONS_ENABLED,
    MISSING_CODEX_CLI_POLICY,
    OCR_INVOCATION_ORDER,
    UNANSWERED_QUESTION_DEFAULT,
    WEARABLE_UNKNOWN_NEVER_TRIGGERS,
    ConfigError,
    OwnerHandoffConfig,
    load_owner_handoff_config,
)


class OwnerHandoffConfigDefaultsTest(unittest.TestCase):
    def test_defaults_match_master_spec(self) -> None:
        config = load_owner_handoff_config(env={})
        self.assertEqual(config.owner_leave_confirmation_seconds, 10.0)
        self.assertEqual(config.input_idle_threshold_seconds, 5.0)
        self.assertEqual(config.handoff_question_expiration_seconds, 60.0)
        self.assertEqual(config.coding_agent_max_runtime_seconds, 300.0)
        self.assertEqual(config.coding_agent_max_safe_steps, 20)
        self.assertEqual(config.owner_handoff_db_path, Path("data/owner_handoff.sqlite3"))
        self.assertEqual(
            config.workspace_session_root, Path("data/owner_handoff/sessions")
        )
        self.assertEqual(config.ocr_adapter_mode, "noop")
        self.assertEqual(config.wearable_device_allowlist, ())
        self.assertIsInstance(config, OwnerHandoffConfig)


class OwnerHandoffConfigValidOverrideTest(unittest.TestCase):
    def test_valid_overrides_are_applied(self) -> None:
        env = {
            "AI_DESK_V2_LEAVE_CONFIRM_SECONDS": "15",
            "AI_DESK_V2_INPUT_IDLE_SECONDS": "3.5",
            "AI_DESK_V2_QUESTION_EXPIRY_SECONDS": "45",
            "AI_DESK_V2_CODING_MAX_RUNTIME_SECONDS": "120",
            "AI_DESK_V2_CODING_MAX_STEPS": "10",
            "AI_DESK_V2_DB_PATH": "data/custom/owner_handoff.sqlite3",
            "AI_DESK_V2_SESSION_ROOT": "data/custom/sessions",
            "AI_DESK_V2_OCR_MODE": "mock",
            "AI_DESK_V2_WEARABLE_DEVICE_IDS": "wearable-1,wearable-2",
        }
        config = load_owner_handoff_config(env=env)
        self.assertEqual(config.owner_leave_confirmation_seconds, 15.0)
        self.assertEqual(config.input_idle_threshold_seconds, 3.5)
        self.assertEqual(config.handoff_question_expiration_seconds, 45.0)
        self.assertEqual(config.coding_agent_max_runtime_seconds, 120.0)
        self.assertEqual(config.coding_agent_max_safe_steps, 10)
        self.assertEqual(
            config.owner_handoff_db_path, Path("data/custom/owner_handoff.sqlite3")
        )
        self.assertEqual(config.workspace_session_root, Path("data/custom/sessions"))
        self.assertEqual(config.ocr_adapter_mode, "mock")
        self.assertEqual(config.wearable_device_allowlist, ("wearable-1", "wearable-2"))


class OwnerHandoffConfigWearableAllowlistTest(unittest.TestCase):
    def test_comma_separated_ids_are_trimmed_and_deduplicated(self) -> None:
        config = load_owner_handoff_config(
            env={"AI_DESK_V2_WEARABLE_DEVICE_IDS": " wearable-1, wearable-2 ,wearable-1,,  "}
        )
        self.assertEqual(config.wearable_device_allowlist, ("wearable-1", "wearable-2"))

    def test_empty_default_rejects_everything(self) -> None:
        config = load_owner_handoff_config(env={})
        self.assertEqual(config.wearable_device_allowlist, ())


class OwnerHandoffConfigInvalidOverrideTest(unittest.TestCase):
    def test_non_numeric_duration_raises_clearly(self) -> None:
        with self.assertRaises(ConfigError):
            load_owner_handoff_config(env={"AI_DESK_V2_LEAVE_CONFIRM_SECONDS": "soon"})

    def test_zero_or_negative_duration_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            load_owner_handoff_config(env={"AI_DESK_V2_QUESTION_EXPIRY_SECONDS": "0"})
        with self.assertRaises(ConfigError):
            load_owner_handoff_config(
                env={"AI_DESK_V2_CODING_MAX_RUNTIME_SECONDS": "-5"}
            )

    def test_non_finite_duration_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            load_owner_handoff_config(env={"AI_DESK_V2_LEAVE_CONFIRM_SECONDS": "inf"})

    def test_non_positive_step_count_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            load_owner_handoff_config(env={"AI_DESK_V2_CODING_MAX_STEPS": "0"})
        with self.assertRaises(ConfigError):
            load_owner_handoff_config(env={"AI_DESK_V2_CODING_MAX_STEPS": "not-a-number"})

    def test_empty_path_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            load_owner_handoff_config(env={"AI_DESK_V2_DB_PATH": "   "})

    def test_filesystem_root_path_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            load_owner_handoff_config(env={"AI_DESK_V2_SESSION_ROOT": "/"})

    def test_unknown_ocr_mode_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            load_owner_handoff_config(env={"AI_DESK_V2_OCR_MODE": "real"})


class OwnerHandoffSafetyInvariantTest(unittest.TestCase):
    """Safety invariants must never be read from the environment."""

    def test_invariants_are_fixed_regardless_of_environment(self) -> None:
        # Even if a caller's environment mapping happens to contain keys
        # that look like they might control these invariants, there is no
        # code path in load_owner_handoff_config that reads them: the
        # invariants are plain module constants.
        env = {
            "AI_DESK_V2_WEARABLE_UNKNOWN_TRIGGERS": "true",
            "AI_DESK_V2_CONTRADICTION_POLICY": "act",
            "AI_DESK_V2_EXTERNAL_ACTIONS_ENABLED": "true",
            "AI_DESK_V2_OCR_POLICY": "always",
            "AI_DESK_V2_UNANSWERED_DEFAULT": "A",
            "AI_DESK_V2_MISSING_CODEX_POLICY": "proceed_unsandboxed",
        }
        load_owner_handoff_config(env=env)  # must not raise, and must not be read

        self.assertIs(WEARABLE_UNKNOWN_NEVER_TRIGGERS, True)
        self.assertEqual(CONTRADICTORY_SIGNALS_POLICY, "wait")
        self.assertIs(EXTERNAL_ACTIONS_ENABLED, False)
        self.assertEqual(
            OCR_INVOCATION_ORDER,
            ("activity_classifier", "context_analyzer", "ocr"),
        )
        self.assertEqual(UNANSWERED_QUESTION_DEFAULT, "D")
        self.assertEqual(MISSING_CODEX_CLI_POLICY, "stop_safely")

    def test_no_env_vars_exist_for_removed_unsafe_overrides(self) -> None:
        import application.owner_handoff.config as config_module

        source = Path(config_module.__file__).read_text(encoding="utf-8")
        for removed_env_var in (
            "AI_DESK_V2_WEARABLE_UNKNOWN_TRIGGERS",
            "AI_DESK_V2_CONTRADICTION_POLICY",
            "AI_DESK_V2_EXTERNAL_ACTIONS_ENABLED",
            "AI_DESK_V2_OCR_POLICY",
            "AI_DESK_V2_UNANSWERED_DEFAULT",
            "AI_DESK_V2_MISSING_CODEX_POLICY",
        ):
            self.assertNotIn(removed_env_var, source)


if __name__ == "__main__":
    unittest.main()
