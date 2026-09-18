# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Tests for the A2A storage adapter (TeamTaskStorage)."""

from __future__ import annotations

import pytest

from agent_teams.a2a.storage import (
    TeamTaskStorage,
    a2a_artifact_to_team,
    a2a_status_to_team,
    task_to_a2a,
    team_status_to_a2a,
)
from agent_teams.state.artifact_store import InMemoryArtifactStore
from agent_teams.state.task_list import SharedTaskList
from agent_teams.types import (
    Artifact,
    TaskDefinition,
    TaskResult,
    TaskStatus,
)

from fasta2a.schema import (
    Artifact as A2AArtifact,
    Message as A2AMessage,
    Part,
)


# ---------------------------------------------------------------------------
# Status mapping tests
# ---------------------------------------------------------------------------


class TestStatusMapping:
    """Tests for team <-> A2A status conversions."""

    def test_team_pending_to_a2a(self) -> None:
        assert team_status_to_a2a(TaskStatus.PENDING) == "submitted"

    def test_team_assigned_to_a2a(self) -> None:
        assert team_status_to_a2a(TaskStatus.ASSIGNED) == "submitted"

    def test_team_in_progress_to_a2a(self) -> None:
        assert team_status_to_a2a(TaskStatus.IN_PROGRESS) == "working"

    def test_team_completed_to_a2a(self) -> None:
        assert team_status_to_a2a(TaskStatus.COMPLETED) == "completed"

    def test_team_failed_to_a2a(self) -> None:
        assert team_status_to_a2a(TaskStatus.FAILED) == "failed"

    def test_team_cancelled_to_a2a(self) -> None:
        assert team_status_to_a2a(TaskStatus.CANCELLED) == "canceled"

    def test_team_blocked_to_a2a(self) -> None:
        assert team_status_to_a2a(TaskStatus.BLOCKED) == "submitted"

    def test_team_status_string_value(self) -> None:
        assert team_status_to_a2a("in_progress") == "working"

    def test_unknown_team_status_maps_to_unknown(self) -> None:
        assert team_status_to_a2a("bogus") == "unknown"

    def test_a2a_submitted_to_team(self) -> None:
        assert a2a_status_to_team("submitted") == TaskStatus.PENDING

    def test_a2a_working_to_team(self) -> None:
        assert a2a_status_to_team("working") == TaskStatus.IN_PROGRESS

    def test_a2a_completed_to_team(self) -> None:
        assert a2a_status_to_team("completed") == TaskStatus.COMPLETED

    def test_a2a_failed_to_team(self) -> None:
        assert a2a_status_to_team("failed") == TaskStatus.FAILED

    def test_a2a_canceled_to_team(self) -> None:
        assert a2a_status_to_team("canceled") == TaskStatus.CANCELLED

    def test_a2a_input_required_to_team(self) -> None:
        assert a2a_status_to_team("input-required") == TaskStatus.PENDING

    def test_a2a_unknown_defaults_to_pending(self) -> None:
        assert a2a_status_to_team("whatever") == TaskStatus.PENDING


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


class TestConversionHelpers:
    """Tests for task_to_a2a, a2a_artifact_to_team, etc."""

    def test_task_to_a2a_basic(self) -> None:
        task = TaskDefinition(title="Test task", description="Some work")
        a2a_task = task_to_a2a(task, "team-1")

        assert a2a_task["id"] == task.id
        assert a2a_task["context_id"] == "team-1"
        # A2A v1: a task carries no `kind` discriminator.
        assert "kind" not in a2a_task
        assert a2a_task["status"]["state"] == "submitted"
        assert len(a2a_task["history"]) == 1
        assert a2a_task["history"][0]["parts"][0]["text"] == "Test task\n\nSome work"

    def test_task_to_a2a_with_result_artifacts(self) -> None:
        art = Artifact(id="a1", name="output", content="hello world")
        result = TaskResult(
            task_id="t1",
            member_id="m1",
            status=TaskStatus.COMPLETED,
            artifacts=[art],
        )
        task = TaskDefinition(
            id="t1",
            title="Task with result",
            status=TaskStatus.COMPLETED,
            result=result,
        )
        a2a_task = task_to_a2a(task, "team-1")

        assert a2a_task["status"]["state"] == "completed"
        assert "artifacts" in a2a_task
        assert len(a2a_task["artifacts"]) == 1
        assert a2a_task["artifacts"][0]["artifact_id"] == "a1"

    def test_task_to_a2a_without_result(self) -> None:
        task = TaskDefinition(title="No result")
        a2a_task = task_to_a2a(task, "team-x")
        assert "artifacts" not in a2a_task

    def test_a2a_artifact_to_team_text(self) -> None:
        a2a_art = A2AArtifact(
            artifact_id="art-1",
            name="code output",
            parts=[Part(text="print('hello')")],
            metadata={"content_type": "text/python"},
        )
        team_art = a2a_artifact_to_team(a2a_art)

        assert team_art.id == "art-1"
        assert team_art.name == "code output"
        assert team_art.content == "print('hello')"
        assert team_art.content_type == "text/python"

    def test_a2a_artifact_to_team_no_parts(self) -> None:
        a2a_art = A2AArtifact(
            artifact_id="art-2",
            name="empty",
            parts=[],
        )
        team_art = a2a_artifact_to_team(a2a_art)
        assert team_art.content is None

    def test_a2a_artifact_to_team_default_content_type(self) -> None:
        a2a_art = A2AArtifact(
            artifact_id="art-3",
            name="plain",
            parts=[Part(text="stuff")],
        )
        team_art = a2a_artifact_to_team(a2a_art)
        assert team_art.content_type == "text/plain"


# ---------------------------------------------------------------------------
# TeamTaskStorage
# ---------------------------------------------------------------------------


class TestTeamTaskStorage:
    """Tests for TeamTaskStorage — fasta2a Storage backed by agent-teams state."""

    @pytest.fixture
    def task_list(self) -> SharedTaskList:
        return SharedTaskList()

    @pytest.fixture
    def artifact_store(self) -> InMemoryArtifactStore:
        return InMemoryArtifactStore()

    @pytest.fixture
    def storage(
        self,
        task_list: SharedTaskList,
        artifact_store: InMemoryArtifactStore,
    ) -> TeamTaskStorage:
        return TeamTaskStorage(
            task_list=task_list,
            artifact_store=artifact_store,
            team_id="team-test",
        )

    @pytest.mark.asyncio
    async def test_submit_and_load_task(self, storage: TeamTaskStorage) -> None:
        msg = A2AMessage(
            role="user",
            message_id="msg-1",
            parts=[Part(text="Analyse the data\nWith graphs")],
        )

        task = await storage.submit_task("ctx-1", msg)

        assert "kind" not in task
        assert task["context_id"] == "team-test"
        assert task["status"]["state"] == "submitted"

        loaded = await storage.load_task(task["id"])
        assert loaded is not None
        assert loaded["id"] == task["id"]

    @pytest.mark.asyncio
    async def test_load_task_not_found(self, storage: TeamTaskStorage) -> None:
        result = await storage.load_task("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_task_to_working(
        self,
        storage: TeamTaskStorage,
        task_list: SharedTaskList,
    ) -> None:
        msg = A2AMessage(
            role="user",
            message_id="msg-2",
            parts=[Part(text="Do something")],
        )
        task = await storage.submit_task("ctx-2", msg)

        updated = await storage.update_task(task["id"], state="working")
        assert updated["status"]["state"] == "working"

        raw_task = await task_list.get(task["id"])
        assert raw_task is not None
        assert raw_task.status == TaskStatus.IN_PROGRESS

    @pytest.mark.asyncio
    async def test_update_task_to_completed(
        self,
        storage: TeamTaskStorage,
        task_list: SharedTaskList,
    ) -> None:
        msg = A2AMessage(
            role="user",
            message_id="msg-3",
            parts=[Part(text="Task text")],
        )
        task = await storage.submit_task("ctx-3", msg)

        result_msg = A2AMessage(
            role="agent",
            message_id="msg-3-result",
            parts=[Part(text="Done!")],
        )
        updated = await storage.update_task(
            task["id"],
            state="completed",
            new_messages=[result_msg],
        )
        assert updated["status"]["state"] == "completed"

        raw_task = await task_list.get(task["id"])
        assert raw_task is not None
        assert raw_task.status == TaskStatus.COMPLETED
        assert raw_task.result is not None
        assert raw_task.result.output == "Done!"

    @pytest.mark.asyncio
    async def test_update_task_to_failed(
        self,
        storage: TeamTaskStorage,
        task_list: SharedTaskList,
    ) -> None:
        msg = A2AMessage(
            role="user",
            message_id="msg-4",
            parts=[Part(text="Gonna fail")],
        )
        task = await storage.submit_task("ctx-4", msg)

        updated = await storage.update_task(task["id"], state="failed")
        assert updated["status"]["state"] == "failed"

        raw_task = await task_list.get(task["id"])
        assert raw_task is not None
        assert raw_task.status == TaskStatus.FAILED

    @pytest.mark.asyncio
    async def test_update_task_to_canceled(
        self,
        storage: TeamTaskStorage,
        task_list: SharedTaskList,
    ) -> None:
        msg = A2AMessage(
            role="user",
            message_id="msg-5",
            parts=[Part(text="Cancel me")],
        )
        task = await storage.submit_task("ctx-5", msg)

        updated = await storage.update_task(task["id"], state="canceled")
        assert updated["status"]["state"] == "canceled"

    @pytest.mark.asyncio
    async def test_update_task_with_artifacts(
        self,
        storage: TeamTaskStorage,
        artifact_store: InMemoryArtifactStore,
    ) -> None:
        msg = A2AMessage(
            role="user",
            message_id="msg-6",
            parts=[Part(text="Make artifact")],
        )
        task = await storage.submit_task("ctx-6", msg)

        a2a_art = A2AArtifact(
            artifact_id="art-new",
            name="result",
            parts=[Part(text="artifact content")],
        )
        await storage.update_task(
            task["id"],
            state="working",
            new_artifacts=[a2a_art],
        )

        stored = await artifact_store.list("team-test")
        assert len(stored) == 1
        assert stored[0].id == "art-new"
        assert stored[0].content == "artifact content"

    @pytest.mark.asyncio
    async def test_update_task_not_found_raises(
        self, storage: TeamTaskStorage
    ) -> None:
        with pytest.raises(KeyError, match="not found"):
            await storage.update_task("nope", state="working")

    @pytest.mark.asyncio
    async def test_context_load_and_update(self, storage: TeamTaskStorage) -> None:
        result = await storage.load_context("ctx-new")
        assert result is None

        await storage.update_context("ctx-new", {"foo": "bar"})
        loaded = await storage.load_context("ctx-new")
        assert loaded == {"foo": "bar"}

    @pytest.mark.asyncio
    async def test_submit_extracts_title_and_description(
        self,
        storage: TeamTaskStorage,
        task_list: SharedTaskList,
    ) -> None:
        msg = A2AMessage(
            role="user",
            message_id="msg-7",
            parts=[Part(text="My Title\nDescription line")],
        )
        task = await storage.submit_task("ctx-7", msg)

        raw = await task_list.get(task["id"])
        assert raw is not None
        assert raw.title == "My Title"
        assert raw.description == "Description line"
