import tempfile
import unittest
from pathlib import Path

from application.owner_handoff.state_machine import (
    OwnerHandoffState,
    PermissionKind,
    PermissionKindError,
    TransitionError,
    validate_transition,
)
from application.owner_handoff.store import (
    EventConflictError,
    OwnerHandoffStore,
    StaleTransitionError,
)

S = OwnerHandoffState
P = PermissionKind


def _research_path(prefix: str) -> list[tuple]:
    """OBSERVING -> ... -> EXECUTING via the research skill (no duplication,
    no permission gate)."""

    return [
        (f"{prefix}-1", S.OBSERVING, S.LEFT_CANDIDATE, "partial absence signal detected", None),
        (f"{prefix}-2", S.LEFT_CANDIDATE, S.OWNER_LEFT_CONFIRMED, "10s confirmation window elapsed", None),
        (f"{prefix}-3", S.OWNER_LEFT_CONFIRMED, S.QUESTION_PENDING, "handoff question generated", None),
        (f"{prefix}-4", S.QUESTION_PENDING, S.AUTHORIZED, "owner selected option A", None),
        (f"{prefix}-5", S.AUTHORIZED, S.EXECUTING, "research task delegated", None),
    ]


def _coding_path_to_executing(prefix: str) -> list[tuple]:
    """OBSERVING -> ... -> EXECUTING via the coding skill, including the
    corrected CODEX_DATA authorization gate."""

    return [
        (f"{prefix}-1", S.OBSERVING, S.LEFT_CANDIDATE, "partial absence signal detected", None),
        (f"{prefix}-2", S.LEFT_CANDIDATE, S.OWNER_LEFT_CONFIRMED, "10s confirmation window elapsed", None),
        (f"{prefix}-3", S.OWNER_LEFT_CONFIRMED, S.QUESTION_PENDING, "handoff question generated", None),
        (f"{prefix}-4", S.QUESTION_PENDING, S.AUTHORIZED, "owner selected option B", None),
        (f"{prefix}-5", S.AUTHORIZED, S.WORKSPACE_DUPLICATING, "coding skill selected", None),
        (
            f"{prefix}-6",
            S.WORKSPACE_DUPLICATING,
            S.PERMISSION_PENDING,
            "duplicate workspace ready; requesting codex data authorization",
            P.CODEX_DATA,
        ),
        (
            f"{prefix}-7",
            S.PERMISSION_PENDING,
            S.EXECUTING,
            "codex data authorization granted",
            P.CODEX_DATA,
        ),
    ]


def _coding_path_to_permission_pending(prefix: str) -> list[tuple]:
    """Like ``_coding_path_to_executing`` but stops with the task still
    sitting in PERMISSION_PENDING(CODEX_DATA) — useful for restart-recovery
    scenarios."""

    return _coding_path_to_executing(prefix)[:6]


def _drive(store: OwnerHandoffStore, task_id: str, steps: list[tuple]):
    record = None
    for event_id, from_state, to_state, reason, permission_kind in steps:
        record = store.apply_transition(
            task_id=task_id,
            event_id=event_id,
            from_state=from_state,
            to_state=to_state,
            reason=reason,
            permission_kind=permission_kind,
        )
    return record


class StateMachineAllowedTransitionsTest(unittest.TestCase):
    """Every row of the Master Spec / Phase 0 corrected transition table."""

    def test_every_allowed_transition_validates(self) -> None:
        allowed = [
            (S.OBSERVING, S.LEFT_CANDIDATE, None),
            (S.LEFT_CANDIDATE, S.OBSERVING, None),
            (S.LEFT_CANDIDATE, S.OWNER_LEFT_CONFIRMED, None),
            (S.OWNER_LEFT_CONFIRMED, S.QUESTION_PENDING, None),
            (S.QUESTION_PENDING, S.AUTHORIZED, None),
            (S.QUESTION_PENDING, S.CANCELED, None),
            (S.AUTHORIZED, S.WORKSPACE_DUPLICATING, None),
            (S.AUTHORIZED, S.EXECUTING, None),
            (S.WORKSPACE_DUPLICATING, S.PERMISSION_PENDING, P.CODEX_DATA),
            (S.WORKSPACE_DUPLICATING, S.FAILED, None),
            (S.PERMISSION_PENDING, S.EXECUTING, P.CODEX_DATA),
            (S.PERMISSION_PENDING, S.CANCELED, P.CODEX_DATA),
            (S.EXECUTING, S.PERMISSION_PENDING, P.PACKAGE_INSTALL),
            (S.EXECUTING, S.RETURN_REQUESTED, None),
            (S.EXECUTING, S.READY_FOR_REVIEW, None),
            (S.EXECUTING, S.FAILED, None),
            (S.RETURN_REQUESTED, S.READY_FOR_REVIEW, None),
            (S.READY_FOR_REVIEW, S.RETURNED, None),
            (S.CANCELED, S.OBSERVING, None),
            (S.FAILED, S.OBSERVING, None),
            (S.RETURNED, S.OBSERVING, None),
        ]
        for source, target, permission_kind in allowed:
            with self.subTest(source=source, target=target):
                validate_transition(
                    source=source, target=target, permission_kind=permission_kind
                )


class StateMachineForbiddenTransitionTest(unittest.TestCase):
    def test_observing_cannot_jump_to_executing(self) -> None:
        with self.assertRaises(TransitionError):
            validate_transition(source=S.OBSERVING, target=S.EXECUTING)

    def test_workspace_duplicating_cannot_go_directly_to_executing(self) -> None:
        """Phase 1 review correction: Codex data authorization is mandatory
        between WORKSPACE_DUPLICATING and EXECUTING."""
        with self.assertRaises(TransitionError):
            validate_transition(source=S.WORKSPACE_DUPLICATING, target=S.EXECUTING)

    def test_entering_permission_pending_requires_a_permission_kind(self) -> None:
        with self.assertRaises(PermissionKindError):
            validate_transition(
                source=S.WORKSPACE_DUPLICATING,
                target=S.PERMISSION_PENDING,
                permission_kind=None,
            )

    def test_entering_permission_pending_from_workspace_duplicating_requires_codex_data(
        self,
    ) -> None:
        with self.assertRaises(PermissionKindError):
            validate_transition(
                source=S.WORKSPACE_DUPLICATING,
                target=S.PERMISSION_PENDING,
                permission_kind=P.PACKAGE_INSTALL,
            )

    def test_entering_permission_pending_from_executing_requires_package_install(
        self,
    ) -> None:
        with self.assertRaises(PermissionKindError):
            validate_transition(
                source=S.EXECUTING,
                target=S.PERMISSION_PENDING,
                permission_kind=P.CODEX_DATA,
            )

    def test_leaving_permission_pending_requires_a_permission_kind(self) -> None:
        with self.assertRaises(PermissionKindError):
            validate_transition(
                source=S.PERMISSION_PENDING, target=S.EXECUTING, permission_kind=None
            )
        with self.assertRaises(PermissionKindError):
            validate_transition(
                source=S.PERMISSION_PENDING, target=S.CANCELED, permission_kind=None
            )

    def test_unrelated_transition_rejects_any_nonnull_permission_kind(self) -> None:
        with self.assertRaises(PermissionKindError):
            validate_transition(
                source=S.OBSERVING,
                target=S.LEFT_CANDIDATE,
                permission_kind=P.CODEX_DATA,
            )
        with self.assertRaises(PermissionKindError):
            validate_transition(
                source=S.AUTHORIZED,
                target=S.EXECUTING,
                permission_kind=P.PACKAGE_INSTALL,
            )


class OwnerHandoffStoreTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "owner_handoff.sqlite3"
        self.store = OwnerHandoffStore(self.db_path)

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()


class OwnerHandoffStoreCreateTaskTest(OwnerHandoffStoreTestBase):
    def test_create_task_always_starts_at_observing(self) -> None:
        self.store.create_task("task-1")
        self.assertEqual(self.store.get_task("task-1").state, S.OBSERVING)
        self.assertIsNone(self.store.get_task("task-1").permission_kind)

    def test_create_task_does_not_accept_an_initial_state_argument(self) -> None:
        with self.assertRaises(TypeError):
            self.store.create_task("task-1", initial_state=S.EXECUTING)  # type: ignore[call-arg]


class OwnerHandoffStoreNormalPathTest(OwnerHandoffStoreTestBase):
    def test_full_coding_path_with_codex_data_then_install_permission(self) -> None:
        self.store.create_task("task-1")
        record = _drive(self.store, "task-1", _coding_path_to_executing("t1"))
        self.assertEqual(record.state, S.EXECUTING)
        self.assertIsNone(record.permission_kind)

        # Mid-execution package install request/approval.
        self.store.apply_transition(
            task_id="task-1",
            event_id="t1-8",
            from_state=S.EXECUTING,
            to_state=S.PERMISSION_PENDING,
            reason="install permission requested",
            permission_kind=P.PACKAGE_INSTALL,
        )
        record = self.store.apply_transition(
            task_id="task-1",
            event_id="t1-9",
            from_state=S.PERMISSION_PENDING,
            to_state=S.EXECUTING,
            reason="install permission granted",
            permission_kind=P.PACKAGE_INSTALL,
        )
        self.assertEqual(record.state, S.EXECUTING)
        self.assertIsNone(record.permission_kind)
        self.assertEqual(len(self.store.history("task-1")), 9)

    def test_research_path_skips_workspace_duplication(self) -> None:
        self.store.create_task("task-2")
        record = _drive(self.store, "task-2", _research_path("t2"))
        self.assertEqual(record.state, S.EXECUTING)
        self.assertIsNone(record.permission_kind)


class OwnerHandoffStorePermissionKindTest(OwnerHandoffStoreTestBase):
    """Repair: permission_kind must be auditable and enforced against the
    task's actually persisted pending kind, not merely trusted."""

    def test_permission_kind_is_persisted_while_pending(self) -> None:
        self.store.create_task("task-1")
        _drive(self.store, "task-1", _coding_path_to_permission_pending("t1"))
        self.assertEqual(
            self.store.get_task("task-1").permission_kind, P.CODEX_DATA
        )

    def test_mismatched_permission_kind_when_leaving_is_rejected(self) -> None:
        self.store.create_task("task-1")
        _drive(self.store, "task-1", _coding_path_to_permission_pending("t1"))
        with self.assertRaises(PermissionKindError):
            self.store.apply_transition(
                task_id="task-1",
                event_id="t1-wrong-kind",
                from_state=S.PERMISSION_PENDING,
                to_state=S.EXECUTING,
                reason="codex data authorization granted",
                permission_kind=P.PACKAGE_INSTALL,  # wrong: task is pending CODEX_DATA
            )
        # Rejected attempt must not have changed anything.
        self.assertEqual(
            self.store.get_task("task-1").state, S.PERMISSION_PENDING
        )
        self.assertEqual(
            self.store.get_task("task-1").permission_kind, P.CODEX_DATA
        )

    def test_permission_kind_is_cleared_after_leaving_permission_pending(self) -> None:
        self.store.create_task("task-1")
        record = _drive(self.store, "task-1", _coding_path_to_executing("t1"))
        self.assertEqual(record.state, S.EXECUTING)
        self.assertIsNone(record.permission_kind)

    def test_event_leaving_permission_pending_records_the_confirmed_kind(self) -> None:
        self.store.create_task("task-1")
        _drive(self.store, "task-1", _coding_path_to_executing("t1"))
        history = self.store.history("task-1")
        leaving_event = next(
            event for event in history if event.event_id == "t1-7"
        )
        self.assertEqual(leaving_event.from_state, S.PERMISSION_PENDING)
        self.assertEqual(leaving_event.to_state, S.EXECUTING)
        self.assertEqual(leaving_event.permission_kind, P.CODEX_DATA)

    def test_cancel_from_permission_pending_also_requires_matching_kind(self) -> None:
        self.store.create_task("task-1")
        _drive(self.store, "task-1", _coding_path_to_permission_pending("t1"))
        with self.assertRaises(PermissionKindError):
            self.store.apply_transition(
                task_id="task-1",
                event_id="t1-cancel-wrong-kind",
                from_state=S.PERMISSION_PENDING,
                to_state=S.CANCELED,
                reason="codex data authorization denied",
                permission_kind=P.PACKAGE_INSTALL,
            )
        record = self.store.apply_transition(
            task_id="task-1",
            event_id="t1-cancel-right-kind",
            from_state=S.PERMISSION_PENDING,
            to_state=S.CANCELED,
            reason="codex data authorization denied",
            permission_kind=P.CODEX_DATA,
        )
        self.assertEqual(record.state, S.CANCELED)
        self.assertIsNone(record.permission_kind)


class OwnerHandoffStoreRejectionPathTest(OwnerHandoffStoreTestBase):
    def test_forbidden_transition_is_rejected_before_touching_the_database(self) -> None:
        self.store.create_task("task-1")
        with self.assertRaises(TransitionError):
            self.store.apply_transition(
                task_id="task-1",
                event_id="e1",
                from_state=S.OBSERVING,
                to_state=S.EXECUTING,
                reason="invalid",
            )
        self.assertEqual(self.store.get_task("task-1").state, S.OBSERVING)
        self.assertEqual(self.store.history("task-1"), [])

    def test_stale_source_state_is_rejected(self) -> None:
        self.store.create_task("task-1")
        self.store.apply_transition(
            task_id="task-1",
            event_id="e1",
            from_state=S.OBSERVING,
            to_state=S.LEFT_CANDIDATE,
            reason="partial absence signal detected",
        )
        with self.assertRaises(StaleTransitionError):
            self.store.apply_transition(
                task_id="task-1",
                event_id="e2",
                from_state=S.OBSERVING,  # actual state is now LEFT_CANDIDATE
                to_state=S.LEFT_CANDIDATE,
                reason="stale replay attempt",
            )


class OwnerHandoffStoreIdempotencyTest(OwnerHandoffStoreTestBase):
    def test_same_event_id_replay_is_idempotent(self) -> None:
        self.store.create_task("task-1")
        first = self.store.apply_transition(
            task_id="task-1",
            event_id="e1",
            from_state=S.OBSERVING,
            to_state=S.LEFT_CANDIDATE,
            reason="partial absence signal detected",
        )
        second = self.store.apply_transition(
            task_id="task-1",
            event_id="e1",
            from_state=S.OBSERVING,
            to_state=S.LEFT_CANDIDATE,
            reason="partial absence signal detected",
        )
        self.assertEqual(first, second)
        # Exactly one history row, not two.
        self.assertEqual(len(self.store.history("task-1")), 1)

    def test_event_id_reused_for_different_transition_raises_conflict(self) -> None:
        self.store.create_task("task-1")
        self.store.apply_transition(
            task_id="task-1",
            event_id="e1",
            from_state=S.OBSERVING,
            to_state=S.LEFT_CANDIDATE,
            reason="partial absence signal detected",
        )
        with self.assertRaises(EventConflictError):
            self.store.apply_transition(
                task_id="task-1",
                event_id="e1",
                from_state=S.LEFT_CANDIDATE,
                to_state=S.OBSERVING,
                reason="different transition, same event id",
            )

    def test_event_id_reused_with_only_a_different_reason_raises_conflict(self) -> None:
        """Repair: reason is part of the semantic-event identity."""
        self.store.create_task("task-1")
        self.store.apply_transition(
            task_id="task-1",
            event_id="e1",
            from_state=S.OBSERVING,
            to_state=S.LEFT_CANDIDATE,
            reason="partial absence signal detected",
        )
        with self.assertRaises(EventConflictError):
            self.store.apply_transition(
                task_id="task-1",
                event_id="e1",
                from_state=S.OBSERVING,
                to_state=S.LEFT_CANDIDATE,
                reason="a completely different reason",
            )

    def test_replay_does_not_overwrite_the_originally_recorded_occurred_at(self) -> None:
        """occurred_at is a store-assigned timestamp, not caller identity
        data: a replay's occurred_at (or lack of one) never changes what was
        first recorded."""
        import datetime as dt

        self.store.create_task("task-1")
        first_time = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        self.store.apply_transition(
            task_id="task-1",
            event_id="e1",
            from_state=S.OBSERVING,
            to_state=S.LEFT_CANDIDATE,
            reason="partial absence signal detected",
            occurred_at=first_time,
        )
        later_time = dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc)
        self.store.apply_transition(
            task_id="task-1",
            event_id="e1",
            from_state=S.OBSERVING,
            to_state=S.LEFT_CANDIDATE,
            reason="partial absence signal detected",
            occurred_at=later_time,
        )
        history = self.store.history("task-1")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].occurred_at, first_time)


class OwnerHandoffStoreRestartRecoveryTest(OwnerHandoffStoreTestBase):
    def test_importing_or_constructing_the_store_never_triggers_recovery(self) -> None:
        self.store.create_task("task-1")
        _drive(self.store, "task-1", _research_path("t1"))
        # A brand-new store handle pointed at the same file must not
        # silently recover anything just by being constructed.
        reopened = OwnerHandoffStore(self.db_path)
        self.assertEqual(reopened.get_task("task-1").state, S.EXECUTING)

    def test_runtime_bound_state_without_live_handle_is_force_failed(self) -> None:
        self.store.create_task("task-1")
        _drive(self.store, "task-1", _research_path("t1"))  # -> EXECUTING
        self.store.create_task("task-2")
        _drive(
            self.store, "task-2", _coding_path_to_permission_pending("t2")
        )  # -> PERMISSION_PENDING(CODEX_DATA)
        self.store.create_task("task-3")  # stays at OBSERVING

        recovered = self.store.recover_after_restart()
        recovered_ids = {record.task_id for record in recovered}

        self.assertEqual(recovered_ids, {"task-1", "task-2"})
        self.assertEqual(self.store.get_task("task-1").state, S.FAILED)
        self.assertEqual(self.store.get_task("task-2").state, S.FAILED)
        self.assertEqual(self.store.get_task("task-3").state, S.OBSERVING)

        history = self.store.history("task-1")
        self.assertEqual(history[-1].reason, "process restarted mid-execution")

    def test_recovery_from_permission_pending_records_old_kind_and_clears_it(
        self,
    ) -> None:
        self.store.create_task("task-1")
        _drive(self.store, "task-1", _coding_path_to_permission_pending("t1"))
        self.assertEqual(
            self.store.get_task("task-1").permission_kind, P.CODEX_DATA
        )

        self.store.recover_after_restart()

        record = self.store.get_task("task-1")
        self.assertEqual(record.state, S.FAILED)
        self.assertIsNone(record.permission_kind)

        recovery_event = self.store.history("task-1")[-1]
        self.assertEqual(recovery_event.to_state, S.FAILED)
        self.assertEqual(recovery_event.permission_kind, P.CODEX_DATA)

    def test_recovery_is_idempotent_and_produces_one_history_row(self) -> None:
        self.store.create_task("task-1")
        _drive(self.store, "task-1", _research_path("t1"))
        self.store.recover_after_restart()
        history_len_after_first = len(self.store.history("task-1"))
        self.store.recover_after_restart()
        self.assertEqual(len(self.store.history("task-1")), history_len_after_first)
        self.assertEqual(self.store.get_task("task-1").state, S.FAILED)

    def test_two_independent_restart_recovery_lifecycles_both_reach_failed(
        self,
    ) -> None:
        """Repair: the same task must be able to enter EXECUTING, be
        recovered to FAILED, reset to OBSERVING, complete another lifecycle,
        enter EXECUTING again, and be recovered to FAILED again — each
        recovery with a unique, auditable event_id."""

        counter = {"n": 0}

        def factory(record) -> str:
            counter["n"] += 1
            return f"recovery-{counter['n']}"

        self.store.create_task("task-1")
        _drive(self.store, "task-1", _research_path("lifecycle1"))
        self.assertEqual(self.store.get_task("task-1").state, S.EXECUTING)

        first_recovery = self.store.recover_after_restart(event_id_factory=factory)
        self.assertEqual(self.store.get_task("task-1").state, S.FAILED)
        self.assertEqual(len(first_recovery), 1)

        # Repeating the same call must remain idempotent (no new row).
        self.store.recover_after_restart(event_id_factory=factory)
        history_after_first_recovery = self.store.history("task-1")

        # Reset and complete a second, independent lifecycle.
        self.store.apply_transition(
            task_id="task-1",
            event_id="reset-1",
            from_state=S.FAILED,
            to_state=S.OBSERVING,
            reason="cycle reset after terminal state",
        )
        self.assertEqual(self.store.get_task("task-1").state, S.OBSERVING)

        _drive(self.store, "task-1", _research_path("lifecycle2"))
        self.assertEqual(self.store.get_task("task-1").state, S.EXECUTING)

        second_recovery = self.store.recover_after_restart(event_id_factory=factory)
        self.assertEqual(self.store.get_task("task-1").state, S.FAILED)
        self.assertEqual(len(second_recovery), 1)

        final_history = self.store.history("task-1")
        recovery_events = [
            event for event in final_history if event.to_state is S.FAILED
        ]
        self.assertEqual(len(recovery_events), 2)
        self.assertNotEqual(
            recovery_events[0].event_id, recovery_events[1].event_id
        )
        self.assertGreater(len(final_history), len(history_after_first_recovery))


class OwnerHandoffStoreCleanupTest(OwnerHandoffStoreTestBase):
    def test_sqlite_file_is_deletable_after_use(self) -> None:
        self.store.create_task("task-1")
        self.store.apply_transition(
            task_id="task-1",
            event_id="e1",
            from_state=S.OBSERVING,
            to_state=S.LEFT_CANDIDATE,
            reason="partial absence signal detected",
        )
        self.store.close()
        # Must not raise (e.g. PermissionError on Windows from a lingering
        # open handle) — no connection is held open between calls.
        self.db_path.unlink()
        self.assertFalse(self.db_path.exists())


if __name__ == "__main__":
    unittest.main()
