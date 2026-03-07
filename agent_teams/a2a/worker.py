# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""A2A Worker implementation for team member agents.

The ``TeamMemberWorker`` is a fasta2a ``Worker`` subclass that executes
tasks by delegating to team member proxies (``MemberProxy``). This allows
the A2A protocol's ``message/send`` and ``message/stream`` endpoints to
trigger task execution through the agent-teams orchestration layer.

For streaming, the worker uses the fasta2a ``StreamingStorageWrapper`` to
emit status updates and artifacts as SSE events.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fasta2a.schema import (
    Artifact as A2AArtifact,
    Message as A2AMessage,
    TaskIdParams,
    TaskSendParams,
    TextPart,
)
from fasta2a.worker import Worker

from ..orchestration.base import MemberProxy
from ..types import TaskStatus

logger = logging.getLogger(__name__)


class TeamMemberWorker(Worker[dict[str, Any]]):
    """A fasta2a Worker that delegates task execution to team member proxies.

    When the broker dispatches a task, this worker:

    1. Extracts the prompt from the A2A message.
    2. Routes to the appropriate ``MemberProxy`` based on task metadata.
    3. Executes the prompt on the member agent.
    4. Updates the task storage with results and artifacts.

    Parameters
    ----------
    broker:
        The fasta2a broker (usually ``InMemoryBroker``).
    storage:
        The storage backend (``TeamTaskStorage`` or ``StreamingStorageWrapper``).
    members:
        Dict mapping member IDs to their ``MemberProxy`` instances.
    default_member_id:
        If no specific member is targeted, use this member.
    """

    def __init__(
        self,
        broker: Any,
        storage: Any,
        members: dict[str, MemberProxy] | None = None,
        default_member_id: str | None = None,
    ) -> None:
        super().__init__(broker=broker, storage=storage)
        self._members = members or {}
        self._default_member_id = default_member_id

    def set_members(self, members: dict[str, MemberProxy]) -> None:
        """Update the member proxy map (called when team starts)."""
        self._members = members

    async def run_task(self, params: TaskSendParams) -> None:
        """Execute a task by routing to a team member.

        Flow:
        1. Parse the A2A message to extract prompt text.
        2. Find the target member (from metadata or default).
        3. Update task status to ``working``.
        4. Call ``member.run(prompt, context)``.
        5. Update task with result or failure.
        """
        task_id = params["id"]
        context_id = params["context_id"]
        message = params["message"]

        # Extract prompt from message
        prompt = self._extract_prompt(message)
        metadata = message.get("metadata", {})

        # Find target member
        member_id = metadata.get("assigned_to") or self._default_member_id
        member = self._members.get(member_id) if member_id else None

        if member is None:
            # Try first available member
            if self._members:
                member = next(iter(self._members.values()))
            else:
                await self.storage.update_task(task_id, state="failed")
                logger.error("No members available for task %s", task_id)
                return

        # Mark task as working
        await self.storage.update_task(task_id, state="working")

        # Load context
        context = await self.storage.load_context(context_id) or {}
        context["task_id"] = task_id

        start_time = time.time()

        try:
            # Execute on the member agent
            result_text = await member.run(prompt, context)
            duration = time.time() - start_time

            # Build result artifacts
            artifacts = self.build_artifacts(result_text)

            # Build result message
            result_message = A2AMessage(
                role="agent",
                kind="message",
                message_id=f"{task_id}-result",
                parts=[TextPart(kind="text", text=result_text)],
                task_id=task_id,
                context_id=context_id,
                metadata={
                    "member_id": member.id,
                    "duration_seconds": duration,
                },
            )

            # Update task with results
            await self.storage.update_task(
                task_id,
                state="completed",
                new_artifacts=artifacts if artifacts else None,
                new_messages=[result_message],
            )

            # Update context with conversation history
            history = context.get("history", [])
            history.append({"role": "user", "content": prompt})
            history.append({"role": "agent", "content": result_text})
            await self.storage.update_context(context_id, {**context, "history": history})

            logger.info(
                "Task %s completed by member %s in %.1fs",
                task_id,
                member.id,
                duration,
            )

        except Exception as exc:
            duration = time.time() - start_time
            logger.exception("Task %s failed on member %s", task_id, member.id)

            error_message = A2AMessage(
                role="agent",
                kind="message",
                message_id=f"{task_id}-error",
                parts=[TextPart(kind="text", text=f"Error: {exc}")],
                task_id=task_id,
                context_id=context_id,
                metadata={"member_id": member.id, "error": str(exc)},
            )

            await self.storage.update_task(
                task_id,
                state="failed",
                new_messages=[error_message],
            )

    async def cancel_task(self, params: TaskIdParams) -> None:
        """Cancel a running task."""
        task_id = params["id"]
        await self.storage.update_task(task_id, state="canceled")
        logger.info("Task %s cancelled", task_id)

    def build_message_history(self, history: list[A2AMessage]) -> list[Any]:
        """Convert A2A message history to a format suitable for the agent."""
        result = []
        for msg in history:
            role = msg.get("role", "user")
            text_parts = []
            for part in msg.get("parts", []):
                if part.get("kind") == "text":
                    text_parts.append(part.get("text", ""))
            if text_parts:
                result.append({"role": role, "content": "\n".join(text_parts)})
        return result

    def build_artifacts(self, result: Any) -> list[A2AArtifact]:
        """Convert agent output to A2A artifacts."""
        if result is None:
            return []

        # Simple text result → single artifact
        if isinstance(result, str):
            return [
                A2AArtifact(
                    artifact_id=f"art-{id(result):x}",
                    name="result",
                    parts=[TextPart(kind="text", text=result)],
                )
            ]

        return []

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_prompt(message: A2AMessage) -> str:
        """Extract text content from an A2A message."""
        parts = []
        for part in message.get("parts", []):
            if part.get("kind") == "text":
                parts.append(part.get("text", ""))
        return "\n".join(parts) if parts else ""
