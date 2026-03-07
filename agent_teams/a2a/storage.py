# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""A2A-compatible storage adapter that bridges agent-teams state with fasta2a Storage.

This module provides ``TeamTaskStorage``, a fasta2a ``Storage`` implementation
that maps between the agent-teams ``TaskDefinition``/``TaskResult``/``Artifact``
Pydantic models and fasta2a's A2A ``Task``/``Message``/``Artifact`` TypedDicts.

The storage wraps an in-process ``SharedTaskList`` and ``ArtifactStore`` so
that the same task data is accessible via both the internal orchestrator and
the A2A protocol endpoints.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fasta2a.schema import (
    Artifact as A2AArtifact,
    Message as A2AMessage,
    Task as A2ATask,
    TaskState as A2ATaskState,
    TaskStatus as A2ATaskStatus,
    TextPart,
)
from fasta2a.storage import Storage

from ..state.artifact_store import ArtifactStore
from ..state.task_list import SharedTaskList
from ..types import (
    Artifact,
    TaskDefinition,
    TaskResult,
    TaskStatus,
)


# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------

_TEAM_TO_A2A_STATUS: dict[str, A2ATaskState] = {
    TaskStatus.PENDING.value: "submitted",
    TaskStatus.ASSIGNED.value: "submitted",
    TaskStatus.IN_PROGRESS.value: "working",
    TaskStatus.COMPLETED.value: "completed",
    TaskStatus.FAILED.value: "failed",
    TaskStatus.CANCELLED.value: "canceled",
    TaskStatus.BLOCKED.value: "submitted",
}


_A2A_TO_TEAM_STATUS: dict[str, TaskStatus] = {
    "submitted": TaskStatus.PENDING,
    "working": TaskStatus.IN_PROGRESS,
    "completed": TaskStatus.COMPLETED,
    "failed": TaskStatus.FAILED,
    "canceled": TaskStatus.CANCELLED,
    "input-required": TaskStatus.PENDING,
    "rejected": TaskStatus.FAILED,
    "auth-required": TaskStatus.PENDING,
    "unknown": TaskStatus.PENDING,
}


def team_status_to_a2a(status: TaskStatus | str) -> A2ATaskState:
    """Convert an agent-teams task status to an A2A task state."""
    val = status.value if isinstance(status, TaskStatus) else status
    return _TEAM_TO_A2A_STATUS.get(val, "unknown")


def a2a_status_to_team(state: A2ATaskState) -> TaskStatus:
    """Convert an A2A task state to an agent-teams task status."""
    return _A2A_TO_TEAM_STATUS.get(state, TaskStatus.PENDING)


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def task_to_a2a(task: TaskDefinition, team_id: str) -> A2ATask:
    """Convert an agent-teams ``TaskDefinition`` to a fasta2a ``Task``."""
    a2a_state = team_status_to_a2a(task.status)
    a2a_status = A2ATaskStatus(
        state=a2a_state,
        timestamp=datetime.utcnow().isoformat(),
    )

    # Build history from task input
    history: list[A2AMessage] = []
    msg = A2AMessage(
        role="user",
        kind="message",
        message_id=str(uuid.uuid4()),
        parts=[TextPart(kind="text", text=f"{task.title}\n\n{task.description}")],
    )
    history.append(msg)

    # Build artifacts from result
    artifacts: list[A2AArtifact] = []
    if task.result and task.result.artifacts:
        for art in task.result.artifacts:
            a2a_art = _artifact_to_a2a(art)
            artifacts.append(a2a_art)

    result: A2ATask = {
        "id": task.id,
        "context_id": team_id,
        "kind": "task",
        "status": a2a_status,
        "history": history,
    }
    if artifacts:
        result["artifacts"] = artifacts
    return result


def _artifact_to_a2a(art: Artifact) -> A2AArtifact:
    """Convert an agent-teams ``Artifact`` to an A2A ``Artifact``."""
    parts: list[Any] = []
    if art.content is not None:
        parts.append(TextPart(kind="text", text=str(art.content)))
    return A2AArtifact(
        artifact_id=art.id,
        name=art.name,
        parts=parts,
        metadata={"content_type": art.content_type},
    )


def a2a_artifact_to_team(a2a_art: A2AArtifact) -> Artifact:
    """Convert an A2A ``Artifact`` to an agent-teams ``Artifact``."""
    content = None
    for part in a2a_art.get("parts", []):
        if part.get("kind") == "text":
            content = part.get("text", "")
            break

    metadata = a2a_art.get("metadata", {})
    return Artifact(
        id=a2a_art["artifact_id"],
        name=a2a_art.get("name", "artifact"),
        content_type=metadata.get("content_type", "text/plain"),
        content=content,
    )


# ---------------------------------------------------------------------------
# TeamTaskStorage — fasta2a Storage backed by agent-teams state
# ---------------------------------------------------------------------------


class TeamTaskStorage(Storage[dict[str, Any]]):
    """A fasta2a ``Storage`` implementation backed by agent-teams state.

    This storage adapter allows fasta2a's A2A endpoints (message/send,
    message/stream, tasks/get) to read and write through the same task
    state that the orchestration engine uses.

    Parameters
    ----------
    task_list:
        The shared task list used by the orchestrator.
    artifact_store:
        The artifact store used by the orchestrator.
    team_id:
        The team ID for scoping artifacts and context.
    """

    def __init__(
        self,
        task_list: SharedTaskList,
        artifact_store: ArtifactStore,
        team_id: str,
    ) -> None:
        self._task_list = task_list
        self._artifact_store = artifact_store
        self._team_id = team_id
        self._contexts: dict[str, dict[str, Any]] = {}

    async def load_task(self, task_id: str, history_length: int | None = None) -> A2ATask | None:
        """Load a task from the shared task list in A2A format."""
        task = await self._task_list.get(task_id)
        if task is None:
            return None
        return task_to_a2a(task, self._team_id)

    async def submit_task(self, context_id: str, message: A2AMessage) -> A2ATask:
        """Submit a new task via the A2A protocol.

        Converts the A2A message into an agent-teams ``TaskDefinition``
        and adds it to the shared task list.
        """
        # Extract text from message parts
        text_parts: list[str] = []
        for part in message.get("parts", []):
            if part.get("kind") == "text":
                text_parts.append(part.get("text", ""))
        full_text = "\n".join(text_parts)

        # Create a TaskDefinition
        lines = full_text.split("\n", 1)
        title = lines[0].strip() if lines else "A2A Task"
        description = lines[1].strip() if len(lines) > 1 else ""

        task = TaskDefinition(
            title=title,
            description=description,
            input_data={"a2a_message": message, "context_id": context_id},
        )
        await self._task_list.add(task)

        return task_to_a2a(task, self._team_id)

    async def update_task(
        self,
        task_id: str,
        state: A2ATaskState,
        new_artifacts: list[A2AArtifact] | None = None,
        new_messages: list[A2AMessage] | None = None,
    ) -> A2ATask:
        """Update a task's state via the A2A protocol.

        Maps A2A state transitions back to agent-teams ``TaskStatus``.
        """
        task = await self._task_list.get(task_id)
        if task is None:
            raise KeyError(f"Task {task_id} not found")

        team_status = a2a_status_to_team(state)

        # Apply status transition
        if team_status == TaskStatus.IN_PROGRESS:
            # Ensure task is assigned before starting
            if task.status == TaskStatus.PENDING:
                await self._task_list.claim(task_id, task.assigned_to or "a2a")
            await self._task_list.start(task_id)
        elif team_status == TaskStatus.COMPLETED:
            # Ensure task is in a completable state
            if task.status == TaskStatus.PENDING:
                await self._task_list.claim(task_id, task.assigned_to or "a2a")
            if task.status == TaskStatus.ASSIGNED:
                await self._task_list.start(task_id)
            result = TaskResult(
                task_id=task_id,
                member_id=task.assigned_to or "",
                status=TaskStatus.COMPLETED,
                output=_extract_text_from_messages(new_messages) if new_messages else None,
            )
            await self._task_list.complete(task_id, result)
        elif team_status == TaskStatus.FAILED:
            await self._task_list.fail(task_id, "Failed via A2A update")
        elif team_status == TaskStatus.CANCELLED:
            await self._task_list.cancel(task_id)

        # Store artifacts
        if new_artifacts:
            for a2a_art in new_artifacts:
                art = a2a_artifact_to_team(a2a_art)
                await self._artifact_store.store(self._team_id, art)

        # Re-read and return
        updated_task = await self._task_list.get(task_id)
        if updated_task is None:
            raise KeyError(f"Task {task_id} disappeared after update")
        return task_to_a2a(updated_task, self._team_id)

    async def load_context(self, context_id: str) -> dict[str, Any] | None:
        """Load conversation context for a task."""
        return self._contexts.get(context_id)

    async def update_context(self, context_id: str, context: dict[str, Any]) -> None:
        """Update conversation context for a task."""
        self._contexts[context_id] = context


def _extract_text_from_messages(messages: list[A2AMessage]) -> str:
    """Extract text content from a list of A2A messages."""
    texts: list[str] = []
    for msg in messages:
        for part in msg.get("parts", []):
            if part.get("kind") == "text":
                texts.append(part.get("text", ""))
    return "\n".join(texts)
