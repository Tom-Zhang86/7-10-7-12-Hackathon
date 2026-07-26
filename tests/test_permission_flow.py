import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from application.owner_handoff.domain.presence import WearableAnswer, WearableButton
from application.owner_handoff.execution.package_installer import (
    PackageInstallRejected,
    PackageInstaller,
    PackageSpecError,
    build_install_argv,
    venv_python_path,
)
from application.owner_handoff.execution.permission import (
    PermissionContextMismatchError,
    PermissionGate,
    PermissionNotGrantedError,
    compute_manifest_hash,
)
from application.owner_handoff.questions.lifecycle import QuestionKind, QuestionLifecycle
from application.owner_handoff.state_machine import OwnerHandoffState, PermissionKind
from application.owner_handoff.store import OwnerHandoffStore
from application.owner_handoff.workspace.duplicator import duplicate_workspace

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
DEVICE_ID = "wearable-001"
S = OwnerHandoffState


def _answer(*, question_id: str, button: WearableButton, **overrides) -> WearableAnswer:
    defaults = dict(device_id=DEVICE_ID, timestamp=NOW + timedelta(seconds=1))
    defaults.update(overrides)
    return WearableAnswer(question_id=question_id, button=button, **defaults)


class _FakeInstallRunner:
    def __init__(self, *, returncode: int = 0) -> None:
        self.calls: list[dict] = []
        self.returncode = returncode

    def run(self, argv, *, cwd):
        self.calls.append({"argv": argv, "cwd": cwd})
        from application.owner_handoff.execution.codex_cli import CommandResult

        return CommandResult(self.returncode, "installed\n")


class PermissionFlowTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.boundary = Path(self.temp_dir.name)
        self.session_root = self.boundary / "sessions"
        self.session_root.mkdir()
        self.source = self.boundary / "project"
        self.source.mkdir()
        (self.source / "main.py").write_text("print(1)\n")

        self.store = OwnerHandoffStore(self.boundary / "owner_handoff.sqlite3")
        self.store.create_task("task-1")
        self._drive_to_workspace_duplicating()

        self.duplicate_path, self.manifest = duplicate_workspace(
            source=self.source,
            allowed_boundary=self.boundary,
            session_root=self.session_root,
            task_id="task-1",
        )
        self.gate = PermissionGate()

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def _drive_to_workspace_duplicating(self) -> None:
        self.store.apply_transition(
            task_id="task-1", event_id="e1", from_state=S.OBSERVING,
            to_state=S.LEFT_CANDIDATE, reason="r1",
        )
        self.store.apply_transition(
            task_id="task-1", event_id="e2", from_state=S.LEFT_CANDIDATE,
            to_state=S.OWNER_LEFT_CONFIRMED, reason="r2",
        )
        self.store.apply_transition(
            task_id="task-1", event_id="e3", from_state=S.OWNER_LEFT_CONFIRMED,
            to_state=S.QUESTION_PENDING, reason="r3",
        )
        self.store.apply_transition(
            task_id="task-1", event_id="e4", from_state=S.QUESTION_PENDING,
            to_state=S.AUTHORIZED, reason="r4",
        )
        self.store.apply_transition(
            task_id="task-1", event_id="e5", from_state=S.AUTHORIZED,
            to_state=S.WORKSPACE_DUPLICATING, reason="r5",
        )

    def _digest(self) -> str:
        return compute_manifest_hash(self.duplicate_path)

    def _drive_to_executing(self) -> None:
        from application.owner_handoff.execution.permission import (
            request_codex_data_permission,
        )

        codex_question = self.gate.build_codex_data_question(
            context_summary="AI Desk", duplicate_path=str(self.duplicate_path),
            expiration_seconds=60, clock=lambda: NOW
        )
        request_codex_data_permission(self.store, "task-1", codex_question)
        lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        lifecycle.start_question(codex_question, kind=QuestionKind.PERMISSION)
        self.gate.grant_from_answer(
            lifecycle=lifecycle,
            answer=_answer(question_id=codex_question.question_id, button=WearableButton.YES),
            store=self.store, task_id="task-1", session_id=self.manifest.session_id,
            duplicate_path=self.duplicate_path, compute_digest=self._digest, clock=lambda: NOW,
        )
        self.assertEqual(self.store.get_task("task-1").state, S.EXECUTING)
        self.gate.consume(
            task_id="task-1", permission_kind=PermissionKind.CODEX_DATA,
            session_id=self.manifest.session_id, duplicate_path=self.duplicate_path,
            live_digest=self._digest(), now=NOW, question_id=codex_question.question_id,
        )


class CodexDataPermissionNormalPathTest(PermissionFlowTestBase):
    def test_correct_yes_grants_and_consume_matches_exact_context(self) -> None:
        from application.owner_handoff.execution.permission import (
            request_codex_data_permission,
        )

        question = self.gate.build_codex_data_question(
            context_summary="AI Desk: draft PR", duplicate_path=str(self.duplicate_path),
            expiration_seconds=60, clock=lambda: NOW
        )
        request_codex_data_permission(self.store, "task-1", question)
        self.assertEqual(self.store.get_task("task-1").state, S.PERMISSION_PENDING)

        lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        lifecycle.start_question(question, kind=QuestionKind.PERMISSION)

        result = self.gate.grant_from_answer(
            lifecycle=lifecycle,
            answer=_answer(question_id=question.question_id, button=WearableButton.YES),
            store=self.store,
            task_id="task-1",
            session_id=self.manifest.session_id,
            duplicate_path=self.duplicate_path,
            compute_digest=self._digest,
            clock=lambda: NOW,
        )
        self.assertTrue(result.accepted)
        self.assertEqual(result.task_record.state, S.EXECUTING)

        # The permit exists but has not been spent yet -- consuming it with
        # the exact matching context succeeds exactly once.
        authorization = self.gate.consume(
            task_id="task-1",
            permission_kind=PermissionKind.CODEX_DATA,
            session_id=self.manifest.session_id,
            duplicate_path=self.duplicate_path,
            live_digest=self._digest(),
            now=NOW,
            question_id=question.question_id,
        )
        self.assertEqual(authorization.task_id, "task-1")

    def test_permit_cannot_be_reused(self) -> None:
        from application.owner_handoff.execution.permission import (
            request_codex_data_permission,
        )

        question = self.gate.build_codex_data_question(
            context_summary="AI Desk", duplicate_path=str(self.duplicate_path),
            expiration_seconds=60, clock=lambda: NOW
        )
        request_codex_data_permission(self.store, "task-1", question)
        lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        lifecycle.start_question(question, kind=QuestionKind.PERMISSION)
        self.gate.grant_from_answer(
            lifecycle=lifecycle,
            answer=_answer(question_id=question.question_id, button=WearableButton.YES),
            store=self.store, task_id="task-1", session_id=self.manifest.session_id,
            duplicate_path=self.duplicate_path, compute_digest=self._digest, clock=lambda: NOW,
        )
        self.gate.consume(
            task_id="task-1", permission_kind=PermissionKind.CODEX_DATA,
            session_id=self.manifest.session_id, duplicate_path=self.duplicate_path,
            live_digest=self._digest(), now=NOW, question_id=question.question_id,
        )
        with self.assertRaises(PermissionNotGrantedError):
            self.gate.consume(
                task_id="task-1", permission_kind=PermissionKind.CODEX_DATA,
                session_id=self.manifest.session_id, duplicate_path=self.duplicate_path,
                live_digest=self._digest(), now=NOW, question_id=question.question_id,
            )

    def test_grant_creation_failure_never_leaves_task_in_executing(self) -> None:
        """If building the grant (here: computing the digest) fails, the
        PERMISSION_PENDING -> EXECUTING transition must never be attempted
        at all -- the task must stay exactly where it was."""

        from application.owner_handoff.execution.permission import (
            request_codex_data_permission,
        )

        question = self.gate.build_codex_data_question(
            context_summary="AI Desk", duplicate_path=str(self.duplicate_path),
            expiration_seconds=60, clock=lambda: NOW
        )
        request_codex_data_permission(self.store, "task-1", question)
        lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        lifecycle.start_question(question, kind=QuestionKind.PERMISSION)

        def _boom() -> str:
            raise RuntimeError("cannot read duplicate")

        with self.assertRaises(RuntimeError):
            self.gate.grant_from_answer(
                lifecycle=lifecycle,
                answer=_answer(question_id=question.question_id, button=WearableButton.YES),
                store=self.store, task_id="task-1", session_id=self.manifest.session_id,
                duplicate_path=self.duplicate_path, compute_digest=_boom, clock=lambda: NOW,
            )
        self.assertEqual(self.store.get_task("task-1").state, S.PERMISSION_PENDING)
        self.assertFalse(
            self.gate.verify_authorization(
                task_id="task-1", permission_kind=PermissionKind.CODEX_DATA,
                session_id=self.manifest.session_id, now=NOW,
            )
        )

    def test_previous_yes_does_not_authorize_a_different_task(self) -> None:
        from application.owner_handoff.execution.permission import (
            request_codex_data_permission,
        )

        question = self.gate.build_codex_data_question(
            context_summary="AI Desk", duplicate_path=str(self.duplicate_path),
            expiration_seconds=60, clock=lambda: NOW
        )
        request_codex_data_permission(self.store, "task-1", question)
        lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        lifecycle.start_question(question, kind=QuestionKind.PERMISSION)
        self.gate.grant_from_answer(
            lifecycle=lifecycle,
            answer=_answer(question_id=question.question_id, button=WearableButton.YES),
            store=self.store, task_id="task-1", session_id=self.manifest.session_id,
            duplicate_path=self.duplicate_path, compute_digest=self._digest, clock=lambda: NOW,
        )
        with self.assertRaises(PermissionNotGrantedError):
            self.gate.consume(
                task_id="task-2", permission_kind=PermissionKind.CODEX_DATA,
                session_id=self.manifest.session_id, duplicate_path=self.duplicate_path,
                live_digest=self._digest(), now=NOW, question_id=question.question_id,
            )

    def test_previous_yes_does_not_authorize_a_different_session(self) -> None:
        from application.owner_handoff.execution.permission import (
            request_codex_data_permission,
        )

        question = self.gate.build_codex_data_question(
            context_summary="AI Desk", duplicate_path=str(self.duplicate_path),
            expiration_seconds=60, clock=lambda: NOW
        )
        request_codex_data_permission(self.store, "task-1", question)
        lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        lifecycle.start_question(question, kind=QuestionKind.PERMISSION)
        self.gate.grant_from_answer(
            lifecycle=lifecycle,
            answer=_answer(question_id=question.question_id, button=WearableButton.YES),
            store=self.store, task_id="task-1", session_id=self.manifest.session_id,
            duplicate_path=self.duplicate_path, compute_digest=self._digest, clock=lambda: NOW,
        )
        with self.assertRaises(PermissionNotGrantedError):
            self.gate.consume(
                task_id="task-1", permission_kind=PermissionKind.CODEX_DATA,
                session_id="a-different-session-id", duplicate_path=self.duplicate_path,
                live_digest=self._digest(), now=NOW, question_id=question.question_id,
            )

    def test_duplicate_tampering_after_grant_invalidates_the_permit(self) -> None:
        """A modified duplicate produces a different live digest -- the
        exact same permit granted for the pre-tamper digest must not match
        anymore (repair item 3 feeding directly into item 1's binding)."""

        from application.owner_handoff.execution.permission import (
            request_codex_data_permission,
        )

        question = self.gate.build_codex_data_question(
            context_summary="AI Desk", duplicate_path=str(self.duplicate_path),
            expiration_seconds=60, clock=lambda: NOW
        )
        request_codex_data_permission(self.store, "task-1", question)
        lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        lifecycle.start_question(question, kind=QuestionKind.PERMISSION)
        self.gate.grant_from_answer(
            lifecycle=lifecycle,
            answer=_answer(question_id=question.question_id, button=WearableButton.YES),
            store=self.store, task_id="task-1", session_id=self.manifest.session_id,
            duplicate_path=self.duplicate_path, compute_digest=self._digest, clock=lambda: NOW,
        )
        # Tamper with the duplicate after the grant was recorded.
        (self.duplicate_path / "main.py").write_text("print('tampered')\n")
        with self.assertRaises(PermissionNotGrantedError):
            self.gate.consume(
                task_id="task-1", permission_kind=PermissionKind.CODEX_DATA,
                session_id=self.manifest.session_id, duplicate_path=self.duplicate_path,
                live_digest=self._digest(), now=NOW, question_id=question.question_id,
            )

    def test_expired_permit_cannot_be_consumed(self) -> None:
        from application.owner_handoff.execution.permission import (
            request_codex_data_permission,
        )

        question = self.gate.build_codex_data_question(
            context_summary="AI Desk", duplicate_path=str(self.duplicate_path),
            expiration_seconds=60, clock=lambda: NOW
        )
        request_codex_data_permission(self.store, "task-1", question)
        lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        lifecycle.start_question(question, kind=QuestionKind.PERMISSION)
        self.gate.grant_from_answer(
            lifecycle=lifecycle,
            answer=_answer(question_id=question.question_id, button=WearableButton.YES),
            store=self.store, task_id="task-1", session_id=self.manifest.session_id,
            duplicate_path=self.duplicate_path, compute_digest=self._digest, clock=lambda: NOW,
        )
        with self.assertRaises(PermissionNotGrantedError):
            self.gate.consume(
                task_id="task-1", permission_kind=PermissionKind.CODEX_DATA,
                session_id=self.manifest.session_id, duplicate_path=self.duplicate_path,
                live_digest=self._digest(), now=NOW + timedelta(seconds=3600), question_id=question.question_id,
            )


class CodexDataPermissionRejectionTest(PermissionFlowTestBase):
    def _pending_question(self):
        from application.owner_handoff.execution.permission import (
            request_codex_data_permission,
        )

        question = self.gate.build_codex_data_question(
            context_summary="AI Desk", duplicate_path=str(self.duplicate_path),
            expiration_seconds=60, clock=lambda: NOW
        )
        request_codex_data_permission(self.store, "task-1", question)
        return question

    def test_no_cancels_and_no_permit_is_ever_recorded(self) -> None:
        question = self._pending_question()
        lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        lifecycle.start_question(question, kind=QuestionKind.PERMISSION)
        result = self.gate.grant_from_answer(
            lifecycle=lifecycle,
            answer=_answer(question_id=question.question_id, button=WearableButton.NO),
            store=self.store, task_id="task-1", session_id=self.manifest.session_id,
            duplicate_path=self.duplicate_path, compute_digest=self._digest, clock=lambda: NOW,
        )
        self.assertTrue(result.accepted)
        self.assertEqual(result.task_record.state, S.CANCELED)
        with self.assertRaises(PermissionNotGrantedError):
            self.gate.consume(
                task_id="task-1", permission_kind=PermissionKind.CODEX_DATA,
                session_id=self.manifest.session_id, duplicate_path=self.duplicate_path,
                live_digest=self._digest(), now=NOW, question_id=question.question_id,
            )

    def test_timeout_cancels(self) -> None:
        from application.owner_handoff.questions.lifecycle import cancel_for_timeout

        question = self._pending_question()
        record = cancel_for_timeout(
            self.store, "task-1", question, now=NOW + timedelta(seconds=61)
        )
        self.assertEqual(record.state, S.CANCELED)

    def test_disconnect_cancels(self) -> None:
        from application.owner_handoff.questions.lifecycle import cancel_for_disconnect

        question = self._pending_question()
        record = cancel_for_disconnect(self.store, "task-1", question)
        self.assertEqual(record.state, S.CANCELED)

    def test_owner_return_cancels(self) -> None:
        from application.owner_handoff.questions.lifecycle import cancel_for_owner_return

        question = self._pending_question()
        record = cancel_for_owner_return(self.store, "task-1", question)
        self.assertEqual(record.state, S.CANCELED)

    def test_restart_recovery_cancels_permission_pending(self) -> None:
        self._pending_question()
        recovered = self.store.recover_after_restart()
        self.assertEqual(self.store.get_task("task-1").state, S.FAILED)
        self.assertEqual(recovered[0].task_id, "task-1")

    def test_stale_wrong_question_id_rejected_and_no_permit_recorded(self) -> None:
        question = self._pending_question()
        lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        lifecycle.start_question(question, kind=QuestionKind.PERMISSION)
        result = self.gate.grant_from_answer(
            lifecycle=lifecycle,
            answer=_answer(question_id="wrong-question-id", button=WearableButton.YES),
            store=self.store, task_id="task-1", session_id=self.manifest.session_id,
            duplicate_path=self.duplicate_path, compute_digest=self._digest, clock=lambda: NOW,
        )
        self.assertFalse(result.accepted)
        self.assertEqual(self.store.get_task("task-1").state, S.PERMISSION_PENDING)
        with self.assertRaises(PermissionNotGrantedError):
            self.gate.consume(
                task_id="task-1", permission_kind=PermissionKind.CODEX_DATA,
                session_id=self.manifest.session_id, duplicate_path=self.duplicate_path,
                live_digest=self._digest(), now=NOW, question_id=question.question_id,
            )

    def test_unknown_device_rejected(self) -> None:
        question = self._pending_question()
        lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        lifecycle.start_question(question, kind=QuestionKind.PERMISSION)
        result = self.gate.grant_from_answer(
            lifecycle=lifecycle,
            answer=_answer(
                question_id=question.question_id, button=WearableButton.YES, device_id="rogue"
            ),
            store=self.store, task_id="task-1", session_id=self.manifest.session_id,
            duplicate_path=self.duplicate_path, compute_digest=self._digest, clock=lambda: NOW,
        )
        self.assertFalse(result.accepted)

    def test_duplicate_answer_rejected(self) -> None:
        question = self._pending_question()
        lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        lifecycle.start_question(question, kind=QuestionKind.PERMISSION)
        answer = _answer(question_id=question.question_id, button=WearableButton.NO)
        self.gate.grant_from_answer(
            lifecycle=lifecycle, answer=answer, store=self.store, task_id="task-1",
            session_id=self.manifest.session_id, duplicate_path=self.duplicate_path,
            compute_digest=self._digest, clock=lambda: NOW,
        )

        different_answer = _answer(
            question_id=question.question_id, button=WearableButton.YES,
            timestamp=NOW + timedelta(seconds=2),
        )
        result = self.gate.grant_from_answer(
            lifecycle=lifecycle, answer=different_answer, store=self.store, task_id="task-1",
            session_id=self.manifest.session_id, duplicate_path=self.duplicate_path,
            compute_digest=self._digest, clock=lambda: NOW,
        )
        self.assertFalse(result.accepted)


class PackageInstallPermissionNormalPathTest(PermissionFlowTestBase):
    def test_correct_yes_authorizes_install_and_argv_matches_exactly(self) -> None:
        from application.owner_handoff.execution.permission import (
            request_package_install_permission,
        )

        self._drive_to_executing()
        argv = build_install_argv(
            package_specs=["requests==2.31.0"], duplicate_path=self.duplicate_path,
            platform_name="Darwin",
        )
        question = self.gate.build_package_install_question(
            context_summary="AI Desk", package_names=("requests==2.31.0",),
            proposed_argv=tuple(argv), duplicate_path=str(self.duplicate_path),
            expiration_seconds=60, clock=lambda: NOW,
        )
        request_package_install_permission(self.store, "task-1", question)
        self.assertEqual(self.store.get_task("task-1").state, S.PERMISSION_PENDING)

        install_runner = _FakeInstallRunner()
        lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        lifecycle.start_question(question, kind=QuestionKind.PERMISSION)
        result = self.gate.grant_from_answer(
            lifecycle=lifecycle,
            answer=_answer(question_id=question.question_id, button=WearableButton.YES),
            store=self.store, task_id="task-1", session_id=self.manifest.session_id,
            duplicate_path=self.duplicate_path, compute_digest=self._digest,
            package_specs=("requests==2.31.0",), argv=tuple(argv), clock=lambda: NOW,
        )
        self.assertTrue(result.accepted)
        self.assertEqual(result.task_record.state, S.EXECUTING)

        # Only now -- after the matching YES -- may the installer actually run.
        self.assertEqual(len(install_runner.calls), 0)
        installer = PackageInstaller(runner=install_runner, platform_name="Darwin", clock=lambda: NOW)
        outcome = installer.install(
            package_specs=["requests==2.31.0"], duplicate_path=self.duplicate_path,
            task_id="task-1", session_id=self.manifest.session_id,
            question_id=question.question_id, permission_gate=self.gate,
        )
        self.assertTrue(outcome.succeeded)
        self.assertEqual(len(install_runner.calls), 1)
        self.assertEqual(list(outcome.argv), argv)

    def test_local_venv_interpreter_used(self) -> None:
        interpreter = venv_python_path(self.duplicate_path, platform_name="Darwin")
        self.assertTrue(str(interpreter).endswith(str(Path(".venv") / "bin" / "python")))


class PackageInstallGrantBindingTest(PermissionFlowTestBase):
    """Repair: grant_from_answer must bind the YES to exactly what the
    pending PermissionQuestion displayed -- a caller cannot pass a
    different package_specs/argv/duplicate_path and have it silently
    accepted, even if the wearable answer itself is a completely
    legitimate YES for the real question_id."""

    def _drive_to_executing(self) -> None:
        from application.owner_handoff.execution.permission import (
            request_codex_data_permission,
        )

        codex_question = self.gate.build_codex_data_question(
            context_summary="AI Desk", duplicate_path=str(self.duplicate_path),
            expiration_seconds=60, clock=lambda: NOW,
        )
        request_codex_data_permission(self.store, "task-1", codex_question)
        lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        lifecycle.start_question(codex_question, kind=QuestionKind.PERMISSION)
        self.gate.grant_from_answer(
            lifecycle=lifecycle,
            answer=_answer(question_id=codex_question.question_id, button=WearableButton.YES),
            store=self.store, task_id="task-1", session_id=self.manifest.session_id,
            duplicate_path=self.duplicate_path, compute_digest=self._digest, clock=lambda: NOW,
        )
        self.gate.consume(
            task_id="task-1", permission_kind=PermissionKind.CODEX_DATA,
            session_id=self.manifest.session_id, duplicate_path=self.duplicate_path,
            live_digest=self._digest(), now=NOW, question_id=codex_question.question_id,
        )

    def test_grant_from_answer_rejects_specs_and_argv_the_owner_never_saw(self) -> None:
        """The pending question displays "safe==1" / "safe-command"; the
        caller then calls grant_from_answer with "evil==9" / "evil-command"
        instead. The answer must be rejected before any transition, and the
        evil command must never become consumable."""

        self._drive_to_executing()
        question = self.gate.build_package_install_question(
            context_summary="AI Desk", package_names=("safe==1",),
            proposed_argv=("safe-command",), duplicate_path=str(self.duplicate_path),
            expiration_seconds=60, clock=lambda: NOW,
        )
        from application.owner_handoff.execution.permission import (
            request_package_install_permission,
        )

        request_package_install_permission(self.store, "task-1", question)
        lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        lifecycle.start_question(question, kind=QuestionKind.PERMISSION)

        with self.assertRaises(PermissionContextMismatchError):
            self.gate.grant_from_answer(
                lifecycle=lifecycle,
                answer=_answer(question_id=question.question_id, button=WearableButton.YES),
                store=self.store, task_id="task-1", session_id=self.manifest.session_id,
                duplicate_path=self.duplicate_path, compute_digest=self._digest,
                package_specs=("evil==9",), argv=("evil-command",), clock=lambda: NOW,
            )

        # No transition happened (state unchanged) and no permit was ever
        # recorded for either the safe or the evil command.
        self.assertEqual(self.store.get_task("task-1").state, S.PERMISSION_PENDING)
        with self.assertRaises(PermissionNotGrantedError):
            self.gate.consume(
                task_id="task-1", permission_kind=PermissionKind.PACKAGE_INSTALL,
                session_id=self.manifest.session_id, duplicate_path=self.duplicate_path,
                live_digest=self._digest(), now=NOW, question_id=question.question_id,
                package_specs=("evil==9",), argv=("evil-command",),
            )
        with self.assertRaises(PermissionNotGrantedError):
            self.gate.consume(
                task_id="task-1", permission_kind=PermissionKind.PACKAGE_INSTALL,
                session_id=self.manifest.session_id, duplicate_path=self.duplicate_path,
                live_digest=self._digest(), now=NOW, question_id=question.question_id,
                package_specs=("safe==1",), argv=("safe-command",),
            )

    def test_grant_from_answer_rejects_wrong_duplicate_path(self) -> None:
        self._drive_to_executing()
        question = self.gate.build_package_install_question(
            context_summary="AI Desk", package_names=("safe==1",),
            proposed_argv=("safe-command",), duplicate_path=str(self.duplicate_path),
            expiration_seconds=60, clock=lambda: NOW,
        )
        from application.owner_handoff.execution.permission import (
            request_package_install_permission,
        )

        request_package_install_permission(self.store, "task-1", question)
        lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        lifecycle.start_question(question, kind=QuestionKind.PERMISSION)

        with self.assertRaises(PermissionContextMismatchError):
            self.gate.grant_from_answer(
                lifecycle=lifecycle,
                answer=_answer(question_id=question.question_id, button=WearableButton.YES),
                store=self.store, task_id="task-1", session_id=self.manifest.session_id,
                duplicate_path=str(self.duplicate_path) + "-different",
                compute_digest=self._digest, package_specs=("safe==1",),
                argv=("safe-command",), clock=lambda: NOW,
            )
        self.assertEqual(self.store.get_task("task-1").state, S.PERMISSION_PENDING)

    def test_grant_from_answer_rejects_nonempty_specs_for_codex_data(self) -> None:
        question = self.gate.build_codex_data_question(
            context_summary="AI Desk", duplicate_path=str(self.duplicate_path),
            expiration_seconds=60, clock=lambda: NOW,
        )
        from application.owner_handoff.execution.permission import (
            request_codex_data_permission,
        )

        request_codex_data_permission(self.store, "task-1", question)
        lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        lifecycle.start_question(question, kind=QuestionKind.PERMISSION)

        with self.assertRaises(PermissionContextMismatchError):
            self.gate.grant_from_answer(
                lifecycle=lifecycle,
                answer=_answer(question_id=question.question_id, button=WearableButton.YES),
                store=self.store, task_id="task-1", session_id=self.manifest.session_id,
                duplicate_path=self.duplicate_path, compute_digest=self._digest,
                package_specs=("sneaky==1",), argv=("sneaky-command",), clock=lambda: NOW,
            )
        self.assertEqual(self.store.get_task("task-1").state, S.PERMISSION_PENDING)


class PackageInstallPermissionRejectionTest(PermissionFlowTestBase):
    def test_install_without_permit_is_rejected_and_runner_never_called(self) -> None:
        install_runner = _FakeInstallRunner()
        installer = PackageInstaller(runner=install_runner, platform_name="Darwin", clock=lambda: NOW)
        with self.assertRaises(PackageInstallRejected):
            installer.install(
                package_specs=["requests==2.31.0"], duplicate_path=self.duplicate_path,
                task_id="task-1", session_id=self.manifest.session_id,
                question_id="permission-none", permission_gate=self.gate,
            )
        self.assertEqual(len(install_runner.calls), 0)

    def test_package_mismatch_rejected_even_with_a_granted_permit(self) -> None:
        from application.owner_handoff.execution.permission import (
            request_package_install_permission,
        )

        self._drive_to_executing()
        self._grant_package_permit(("requests==2.31.0",))
        install_runner = _FakeInstallRunner()
        installer = PackageInstaller(runner=install_runner, platform_name="Darwin", clock=lambda: NOW)
        # A DIFFERENT package than the one actually granted must not be
        # authorized by that grant.
        with self.assertRaises(PackageInstallRejected):
            installer.install(
                package_specs=["numpy==1.26.0"], duplicate_path=self.duplicate_path,
                task_id="task-1", session_id=self.manifest.session_id,
                question_id=self._granted_question_id, permission_gate=self.gate,
            )
        self.assertEqual(len(install_runner.calls), 0)

    def test_permit_reuse_rejected(self) -> None:
        self._drive_to_executing()
        self._grant_package_permit(("requests==2.31.0",))
        install_runner = _FakeInstallRunner()
        installer = PackageInstaller(runner=install_runner, platform_name="Darwin", clock=lambda: NOW)
        installer.install(
            package_specs=["requests==2.31.0"], duplicate_path=self.duplicate_path,
            task_id="task-1", session_id=self.manifest.session_id,
            question_id=self._granted_question_id, permission_gate=self.gate,
        )
        self.assertEqual(len(install_runner.calls), 1)
        with self.assertRaises(PackageInstallRejected):
            installer.install(
                package_specs=["requests==2.31.0"], duplicate_path=self.duplicate_path,
                task_id="task-1", session_id=self.manifest.session_id,
                question_id=self._granted_question_id, permission_gate=self.gate,
            )
        self.assertEqual(len(install_runner.calls), 1)

    def _grant_package_permit(self, package_specs: tuple[str, ...]) -> None:
        from application.owner_handoff.execution.permission import (
            request_package_install_permission,
        )

        argv = build_install_argv(
            package_specs=list(package_specs), duplicate_path=self.duplicate_path,
            platform_name="Darwin",
        )
        question = self.gate.build_package_install_question(
            context_summary="AI Desk", package_names=package_specs,
            proposed_argv=tuple(argv), duplicate_path=str(self.duplicate_path),
            expiration_seconds=60, clock=lambda: NOW,
        )
        self._granted_question_id = question.question_id
        request_package_install_permission(self.store, "task-1", question)
        lifecycle = QuestionLifecycle(device_allowlist=(DEVICE_ID,), clock=lambda: NOW)
        lifecycle.start_question(question, kind=QuestionKind.PERMISSION)
        self.gate.grant_from_answer(
            lifecycle=lifecycle,
            answer=_answer(question_id=question.question_id, button=WearableButton.YES),
            store=self.store, task_id="task-1", session_id=self.manifest.session_id,
            duplicate_path=self.duplicate_path, compute_digest=self._digest,
            package_specs=package_specs, argv=tuple(argv), clock=lambda: NOW,
        )

    def test_forbidden_pip_options_rejected(self) -> None:
        installer = PackageInstaller(runner=_FakeInstallRunner(), platform_name="Darwin")
        with self.assertRaises(PackageSpecError):
            installer.install(
                package_specs=["-e", "."], duplicate_path=self.duplicate_path,
                task_id="task-1", session_id=self.manifest.session_id,
                question_id="q", permission_gate=self.gate,
            )

    def test_requirements_file_rejected(self) -> None:
        installer = PackageInstaller(runner=_FakeInstallRunner(), platform_name="Darwin")
        with self.assertRaises(PackageSpecError):
            installer.install(
                package_specs=["-r", "requirements.txt"], duplicate_path=self.duplicate_path,
                task_id="task-1", session_id=self.manifest.session_id,
                question_id="q", permission_gate=self.gate,
            )

    def test_url_and_local_path_rejected(self) -> None:
        installer = PackageInstaller(runner=_FakeInstallRunner(), platform_name="Darwin")
        with self.assertRaises(PackageSpecError):
            installer.install(
                package_specs=["git+https://example.com/pkg"], duplicate_path=self.duplicate_path,
                task_id="task-1", session_id=self.manifest.session_id,
                question_id="q", permission_gate=self.gate,
            )
        with self.assertRaises(PackageSpecError):
            installer.install(
                package_specs=["./local_pkg"], duplicate_path=self.duplicate_path,
                task_id="task-1", session_id=self.manifest.session_id,
                question_id="q", permission_gate=self.gate,
            )

    def test_command_substitution_rejected(self) -> None:
        installer = PackageInstaller(runner=_FakeInstallRunner(), platform_name="Darwin")
        with self.assertRaises(PackageSpecError):
            installer.install(
                package_specs=["requests; rm -rf /"], duplicate_path=self.duplicate_path,
                task_id="task-1", session_id=self.manifest.session_id,
                question_id="q", permission_gate=self.gate,
            )


if __name__ == "__main__":
    unittest.main()
