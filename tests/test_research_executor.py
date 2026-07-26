import unittest

from application.handoff.models import A2AResult, TaskCapsule
from application.owner_handoff.domain.execution import ExecutionStatus
from application.owner_handoff.execution.research_executor import ResearchAgentExecutor


class FakeA2AClient:
    def __init__(self, *, result: A2AResult | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[TaskCapsule] = []

    def send_task(self, capsule: TaskCapsule) -> A2AResult:
        self.calls.append(capsule)
        if self._error is not None:
            raise self._error
        return self._result


def _success_result(handoff_id: str) -> A2AResult:
    return A2AResult(
        task_id="a2a-task-1",
        context_id="ctx-1",
        protocol_state="completed",
        artifact={
            "schema_version": "aidesk.research-handoff.result.v1",
            "handoff_id": handoff_id,
            "status": "completed",
            "executive_summary": "Found three relevant approaches.",
        },
    )


class ResearchExecutorNormalPathTest(unittest.TestCase):
    def test_existing_task_capsule_schema_is_preserved(self) -> None:
        client = FakeA2AClient()

        def _capture(capsule: TaskCapsule) -> A2AResult:
            client.calls.append(capsule)
            return _success_result(capsule.handoff_id)

        client.send_task = _capture  # type: ignore[assignment]
        executor = ResearchAgentExecutor(client)
        executor.execute(goal_text="Research presence-aware handoff approaches")

        self.assertEqual(len(client.calls), 1)
        capsule = client.calls[0]
        payload = capsule.as_payload()
        self.assertEqual(payload["schema_version"], "aidesk.research-handoff.request.v1")
        self.assertEqual(payload["goal"], "Research presence-aware handoff approaches")
        self.assertIn("handoff_id", payload)

    def test_fake_success_maps_to_completed(self) -> None:
        client = FakeA2AClient()
        client.send_task = lambda capsule: _success_result(capsule.handoff_id)  # type: ignore[assignment]
        executor = ResearchAgentExecutor(client)
        result = executor.execute(goal_text="Research X")
        self.assertEqual(result.status, ExecutionStatus.COMPLETED)
        self.assertIn("relevant approaches", result.summary)

    def test_no_workspace_or_duplicate_data_is_ever_sent(self) -> None:
        """ResearchAgentExecutor.execute has no parameter that could carry a
        workspace path, duplicate path, or file contents -- verified
        structurally by inspecting what was actually sent."""

        client = FakeA2AClient()
        sent_capsules: list[TaskCapsule] = []

        def _capture(capsule: TaskCapsule) -> A2AResult:
            sent_capsules.append(capsule)
            return _success_result(capsule.handoff_id)

        client.send_task = _capture  # type: ignore[assignment]
        executor = ResearchAgentExecutor(client)
        executor.execute(goal_text="Research the presence fusion approach")

        payload = sent_capsules[0].as_payload()
        self.assertNotIn("duplicate", str(payload).lower())
        self.assertNotIn("workspace_path", payload)
        self.assertEqual(set(payload.keys()) - {"inputs"}, {
            "schema_version", "handoff_id", "goal", "expected_output",
            "agent_skill", "constraints", "created_at",
        })


class ResearchExecutorErrorPathTest(unittest.TestCase):
    def test_fake_error_maps_to_failed(self) -> None:
        client = FakeA2AClient(error=RuntimeError("agent unreachable"))
        executor = ResearchAgentExecutor(client)
        result = executor.execute(goal_text="Research X")
        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertIn("agent unreachable", result.summary)

    def test_input_required_status_maps_to_failed_not_completed(self) -> None:
        client = FakeA2AClient()
        client.send_task = lambda capsule: A2AResult(  # type: ignore[assignment]
            task_id="t", context_id="c", protocol_state="input_required",
            artifact={"status": "input_required"},
        )
        executor = ResearchAgentExecutor(client)
        result = executor.execute(goal_text="Research X")
        self.assertEqual(result.status, ExecutionStatus.FAILED)

    def test_no_real_network_call_in_normal_tests(self) -> None:
        """The fake client never imports httpx/a2a -- this test's mere
        successful execution (without any network stack involved) is the
        proof; no real A2AHandoffClient is constructed anywhere here."""

        client = FakeA2AClient()
        client.send_task = lambda capsule: _success_result(capsule.handoff_id)  # type: ignore[assignment]
        executor = ResearchAgentExecutor(client)
        result = executor.execute(goal_text="Research X")
        self.assertEqual(result.status, ExecutionStatus.COMPLETED)

    def test_secret_in_exception_message_is_redacted(self) -> None:
        client = FakeA2AClient(
            error=RuntimeError("auth failed: token=SUPERSECRET123abc")
        )
        executor = ResearchAgentExecutor(client)
        result = executor.execute(goal_text="Research X")
        self.assertEqual(result.status, ExecutionStatus.FAILED)
        self.assertNotIn("SUPERSECRET123abc", result.summary)
        self.assertNotIn("SUPERSECRET123abc", str(result.detail))

    def test_secret_in_executive_summary_is_redacted(self) -> None:
        client = FakeA2AClient()
        client.send_task = lambda capsule: A2AResult(  # type: ignore[assignment]
            task_id="t", context_id="c", protocol_state="completed",
            artifact={
                "status": "completed",
                "executive_summary": "Found the key: api_key=ABCDEFGHIJKL123",
                "handoff_id": capsule.handoff_id,
            },
        )
        executor = ResearchAgentExecutor(client)
        result = executor.execute(goal_text="Research X")
        self.assertNotIn("ABCDEFGHIJKL123", result.summary)


if __name__ == "__main__":
    unittest.main()
