# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Tests for the TeamMemberWorker (fasta2a Worker)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_teams.a2a.worker import TeamMemberWorker
from agent_teams.orchestration.base import MemberProxy
from agent_teams.types import AgentMemberConfig, AgentMemberState

from fasta2a.schema import (
    Artifact as A2AArtifact,
    Message as A2AMessage,
    TaskSendParams,
    TextPart,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_proxy(
    member_id: str = "member-1",
    response: str = "I completed the task.",
) -> MemberProxy:
    """Create a MemberProxy with an echo-style run function."""
    config = AgentMemberConfig(id=member_id, name=member_id)
    state = AgentMemberState(id=member_id, name=member_id, role="worker")

    async def _run(prompt: str, context: dict | None = None) -> str:
        return response

    return MemberProxy(config=config, state=state, _run_fn=_run)


def _make_failing_proxy(member_id: str = "failing-member") -> MemberProxy:
    """Create a MemberProxy that always raises."""
    config = AgentMemberConfig(id=member_id, name=member_id)
    state = AgentMemberState(id=member_id, name=member_id, role="worker")

    async def _run(prompt: str, context: dict | None = None) -> str:
        raise RuntimeError("Agent crashed")

    return MemberProxy(config=config, state=state, _run_fn=_run)


class FakeStorage:
    """Minimal storage mock for testing the worker."""

    def __init__(self) -> None:
        self.updates: list[tuple[str, str, Any, Any]] = []
        self._contexts: dict[str, dict[str, Any]] = {}

    async def update_task(
        self,
        task_id: str,
        state: str,
        new_artifacts: list[Any] | None = None,
        new_messages: list[Any] | None = None,
    ) -> dict[str, Any]:
        self.updates.append((task_id, state, new_artifacts, new_messages))
        return {"id": task_id, "status": {"state": state}, "kind": "task"}

    async def load_context(self, context_id: str) -> dict[str, Any] | None:
        return self._contexts.get(context_id)

    async def update_context(self, context_id: str, context: dict[str, Any]) -> None:
        self._contexts[context_id] = context


class FakeBroker:
    """Minimal broker mock."""
    pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTeamMemberWorker:
    """Tests for TeamMemberWorker."""

    def _make_params(
        self,
        task_id: str = "task-1",
        context_id: str = "ctx-1",
        text: str = "Do something",
        metadata: dict[str, Any] | None = None,
    ) -> TaskSendParams:
        msg = A2AMessage(
            role="user",
            kind="message",
            message_id="msg-1",
            parts=[TextPart(kind="text", text=text)],
        )
        if metadata is not None:
            msg["metadata"] = metadata
        return TaskSendParams(
            id=task_id,
            context_id=context_id,
            message=msg,
        )

    @pytest.mark.asyncio
    async def test_run_task_success(self) -> None:
        storage = FakeStorage()
        broker = FakeBroker()
        member = _make_proxy("agent-1", "Result text")

        worker = TeamMemberWorker(
            broker=broker,
            storage=storage,
            members={"agent-1": member},
            default_member_id="agent-1",
        )

        params = self._make_params()
        await worker.run_task(params)

        # Should have: working → completed
        states = [u[1] for u in storage.updates]
        assert states == ["working", "completed"]

        # Completed update should have artifacts and messages
        _, state, artifacts, messages = storage.updates[-1]
        assert state == "completed"
        assert messages is not None
        assert len(messages) == 1
        assert messages[0]["parts"][0]["text"] == "Result text"

    @pytest.mark.asyncio
    async def test_run_task_with_assigned_member(self) -> None:
        storage = FakeStorage()
        broker = FakeBroker()
        member1 = _make_proxy("m1", "Response from m1")
        member2 = _make_proxy("m2", "Response from m2")

        worker = TeamMemberWorker(
            broker=broker,
            storage=storage,
            members={"m1": member1, "m2": member2},
        )

        params = self._make_params(metadata={"assigned_to": "m2"})
        await worker.run_task(params)

        # Should complete with m2's response
        _, _, _, messages = storage.updates[-1]
        assert messages[0]["parts"][0]["text"] == "Response from m2"

    @pytest.mark.asyncio
    async def test_run_task_failure(self) -> None:
        storage = FakeStorage()
        broker = FakeBroker()
        member = _make_failing_proxy()

        worker = TeamMemberWorker(
            broker=broker,
            storage=storage,
            members={"failing-member": member},
            default_member_id="failing-member",
        )

        params = self._make_params()
        await worker.run_task(params)

        # Should have: working → failed
        states = [u[1] for u in storage.updates]
        assert states == ["working", "failed"]

        _, _, _, messages = storage.updates[-1]
        assert "Error: Agent crashed" in messages[0]["parts"][0]["text"]

    @pytest.mark.asyncio
    async def test_run_task_no_members(self) -> None:
        storage = FakeStorage()
        broker = FakeBroker()

        worker = TeamMemberWorker(
            broker=broker,
            storage=storage,
            members={},
        )

        params = self._make_params()
        await worker.run_task(params)

        # Should fail since no members
        states = [u[1] for u in storage.updates]
        assert "failed" in states

    @pytest.mark.asyncio
    async def test_run_task_uses_first_member_as_fallback(self) -> None:
        storage = FakeStorage()
        broker = FakeBroker()
        member = _make_proxy("only-member", "Fallback response")

        worker = TeamMemberWorker(
            broker=broker,
            storage=storage,
            members={"only-member": member},
            # No default_member_id set, no metadata with assigned_to
        )

        params = self._make_params()
        await worker.run_task(params)

        states = [u[1] for u in storage.updates]
        assert states == ["working", "completed"]

    @pytest.mark.asyncio
    async def test_cancel_task(self) -> None:
        storage = FakeStorage()
        broker = FakeBroker()
        worker = TeamMemberWorker(broker=broker, storage=storage)

        from fasta2a.schema import TaskIdParams
        await worker.cancel_task(TaskIdParams(id="task-99"))

        assert len(storage.updates) == 1
        assert storage.updates[0] == ("task-99", "canceled", None, None)

    @pytest.mark.asyncio
    async def test_set_members(self) -> None:
        storage = FakeStorage()
        broker = FakeBroker()
        worker = TeamMemberWorker(broker=broker, storage=storage, members={})

        member = _make_proxy("new-member", "new response")
        worker.set_members({"new-member": member})

        params = self._make_params(metadata={"assigned_to": "new-member"})
        await worker.run_task(params)

        states = [u[1] for u in storage.updates]
        assert states == ["working", "completed"]

    @pytest.mark.asyncio
    async def test_context_is_updated(self) -> None:
        storage = FakeStorage()
        broker = FakeBroker()
        member = _make_proxy("m1", "Done")

        worker = TeamMemberWorker(
            broker=broker,
            storage=storage,
            members={"m1": member},
            default_member_id="m1",
        )

        params = self._make_params(context_id="ctx-test")
        await worker.run_task(params)

        ctx = storage._contexts.get("ctx-test")
        assert ctx is not None
        assert "history" in ctx
        assert len(ctx["history"]) == 2  # user + agent

    def test_build_artifacts_string(self) -> None:
        storage = FakeStorage()
        broker = FakeBroker()
        worker = TeamMemberWorker(broker=broker, storage=storage)

        artifacts = worker.build_artifacts("hello world")
        assert len(artifacts) == 1
        assert artifacts[0]["name"] == "result"
        assert artifacts[0]["parts"][0]["text"] == "hello world"

    def test_build_artifacts_none(self) -> None:
        storage = FakeStorage()
        broker = FakeBroker()
        worker = TeamMemberWorker(broker=broker, storage=storage)

        assert worker.build_artifacts(None) == []

    def test_build_message_history(self) -> None:
        storage = FakeStorage()
        broker = FakeBroker()
        worker = TeamMemberWorker(broker=broker, storage=storage)

        history = [
            A2AMessage(
                role="user",
                kind="message",
                message_id="h1",
                parts=[TextPart(kind="text", text="Question")],
            ),
            A2AMessage(
                role="agent",
                kind="message",
                message_id="h2",
                parts=[TextPart(kind="text", text="Answer")],
            ),
        ]

        result = worker.build_message_history(history)
        assert len(result) == 2
        assert result[0] == {"role": "user", "content": "Question"}
        assert result[1] == {"role": "agent", "content": "Answer"}

    def test_extract_prompt(self) -> None:
        msg = A2AMessage(
            role="user",
            kind="message",
            message_id="x",
            parts=[
                TextPart(kind="text", text="Part 1"),
                TextPart(kind="text", text="Part 2"),
            ],
        )
        result = TeamMemberWorker._extract_prompt(msg)
        assert result == "Part 1\nPart 2"

    def test_extract_prompt_empty(self) -> None:
        msg = A2AMessage(
            role="user",
            kind="message",
            message_id="x",
            parts=[],
        )
        result = TeamMemberWorker._extract_prompt(msg)
        assert result == ""
