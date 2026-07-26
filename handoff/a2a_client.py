from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from application.handoff.models import A2AResult, TaskCapsule


class A2AHandoffError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentIdentity:
    name: str
    skill_ids: tuple[str, ...]
    protocol_versions: tuple[str, ...]


class A2AHandoffClient:
    """Blocking facade over the official asynchronous A2A 1.x client."""

    def __init__(
        self,
        agent_url: str = "http://127.0.0.1:9110",
        *,
        timeout_seconds: float = 180.0,
    ) -> None:
        parsed = urlsplit(agent_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("agent_url must be an absolute HTTP(S) URL")
        self.agent_url = agent_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def send_task(self, capsule: TaskCapsule) -> A2AResult:
        return asyncio.run(self._send_task(capsule))

    async def _send_task(self, capsule: TaskCapsule) -> A2AResult:
        import httpx
        from google.protobuf.json_format import MessageToDict

        from a2a.client import ClientConfig, create_client
        from a2a.client.card_resolver import A2ACardResolver
        from a2a.helpers import new_data_part, new_text_part
        from a2a.types import Message, Role, SendMessageRequest, TaskState

        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
        ) as http_client:
            resolver = A2ACardResolver(http_client, self.agent_url)
            card = await resolver.get_agent_card()
            identity = AgentIdentity(
                name=card.name,
                skill_ids=tuple(skill.id for skill in card.skills),
                protocol_versions=tuple(
                    interface.protocol_version
                    for interface in card.supported_interfaces
                ),
            )
            if capsule.agent_skill not in identity.skill_ids:
                raise A2AHandoffError(
                    f"Agent '{identity.name}' does not advertise skill "
                    f"'{capsule.agent_skill}'."
                )
            if "1.0" not in identity.protocol_versions:
                raise A2AHandoffError("Agent does not advertise A2A 1.0.")

            config = ClientConfig(
                streaming=False,
                polling=False,
                httpx_client=http_client,
                supported_protocol_bindings=["JSONRPC"],
                accepted_output_modes=["application/json", "text/plain"],
            )
            client = await create_client(card, client_config=config)
            message = Message(
                message_id=uuid4().hex,
                role=Role.ROLE_USER,
                parts=[
                    new_text_part(
                        f"Research this delegated task and prepare a resumable "
                        f"handoff: {capsule.goal}"
                    ),
                    new_data_part(
                        capsule.as_payload(),
                        media_type="application/json",
                    ),
                ],
            )
            request = SendMessageRequest(
                message=message,
                metadata={"skill": capsule.agent_skill},
            )
            final_task = None
            async with client:
                async for response in client.send_message(request):
                    if response.HasField("task"):
                        final_task = response.task
            if final_task is None:
                raise A2AHandoffError("Agent returned no A2A Task.")

            artifact_data: dict[str, Any] | None = None
            for artifact in final_task.artifacts:
                for part in artifact.parts:
                    if part.WhichOneof("content") == "data":
                        value = MessageToDict(part.data)
                        if isinstance(value, dict):
                            artifact_data = value
            if artifact_data is None:
                raise A2AHandoffError("Agent Task contained no structured Artifact.")
            if artifact_data.get("handoff_id") != capsule.handoff_id:
                raise A2AHandoffError("Agent Artifact handoff_id did not match the request.")
            state_name = TaskState.Name(final_task.status.state).lower()
            state_name = state_name.removeprefix("task_state_")
            return A2AResult(
                task_id=final_task.id,
                context_id=final_task.context_id,
                protocol_state=state_name,
                artifact=artifact_data,
            )
