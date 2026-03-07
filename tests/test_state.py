# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Tests for shared state: task list and artifact store."""

from __future__ import annotations

import pytest

from agent_teams.state.artifact_store import InMemoryArtifactStore
from agent_teams.state.task_list import SharedTaskList
from agent_teams.types import (
    Artifact,
    TaskDefinition,
    TaskPriority,
    TaskStatus,
)


class TestSharedTaskList:
    """Test the dependency-aware shared task list."""

    @pytest.fixture
    def tl(self) -> SharedTaskList:
        return SharedTaskList()

    async def test_add_and_get(self, tl: SharedTaskList):
        task = TaskDefinition(title="T1", description="First task")
        await tl.add(task)
        retrieved = await tl.get(task.id)
        assert retrieved is not None
        assert retrieved.title == "T1"

    async def test_get_nonexistent(self, tl: SharedTaskList):
        result = await tl.get("does-not-exist")
        assert result is None

    async def test_remove(self, tl: SharedTaskList):
        task = TaskDefinition(title="T1", description="Remove me")
        await tl.add(task)
        removed = await tl.remove(task.id)
        assert removed is True
        assert await tl.get(task.id) is None

    async def test_remove_nonexistent(self, tl: SharedTaskList):
        removed = await tl.remove("nothing")
        assert removed is False

    async def test_available_returns_pending_unblocked(self, tl: SharedTaskList):
        t1 = TaskDefinition(title="T1", description="Independent")
        t2 = TaskDefinition(title="T2", description="Depends on T1", depends_on=[t1.id])
        await tl.add(t1)
        await tl.add(t2)

        available = await tl.get_available()
        ids = [t.id for t in available]
        assert t1.id in ids
        assert t2.id not in ids  # blocked by dependency

    async def test_available_after_dependency_completed(self, tl: SharedTaskList):
        t1 = TaskDefinition(title="T1", description="Dep")
        t2 = TaskDefinition(title="T2", description="Depends", depends_on=[t1.id])
        await tl.add(t1)
        await tl.add(t2)

        await tl.complete(t1.id, result="done")

        available = await tl.get_available()
        ids = [t.id for t in available]
        assert t2.id in ids  # now unblocked

    async def test_claim(self, tl: SharedTaskList):
        task = TaskDefinition(title="T1", description="Claim me")
        await tl.add(task)

        claimed = await tl.claim(task.id, "worker-1")
        assert claimed is True
        t = await tl.get(task.id)
        assert t.status == TaskStatus.ASSIGNED
        assert t.assigned_to == "worker-1"

    async def test_claim_already_claimed(self, tl: SharedTaskList):
        task = TaskDefinition(title="T1", description="Can't double claim")
        await tl.add(task)
        await tl.claim(task.id, "worker-1")

        claimed = await tl.claim(task.id, "worker-2")
        assert claimed is False

    async def test_start(self, tl: SharedTaskList):
        task = TaskDefinition(title="T1", description="Start me")
        await tl.add(task)
        await tl.claim(task.id, "worker-1")
        await tl.start(task.id)

        t = await tl.get(task.id)
        assert t.status == TaskStatus.IN_PROGRESS

    async def test_complete(self, tl: SharedTaskList):
        task = TaskDefinition(title="T1", description="Complete me")
        await tl.add(task)
        await tl.claim(task.id, "worker-1")
        await tl.start(task.id)
        await tl.complete(task.id, result="Done!")

        t = await tl.get(task.id)
        assert t.status == TaskStatus.COMPLETED

    async def test_fail(self, tl: SharedTaskList):
        task = TaskDefinition(title="T1", description="Fail me")
        await tl.add(task)
        await tl.claim(task.id, "worker-1")
        await tl.start(task.id)
        await tl.fail(task.id, error="Oops")

        t = await tl.get(task.id)
        assert t.status == TaskStatus.FAILED

    async def test_cancel(self, tl: SharedTaskList):
        task = TaskDefinition(title="T1", description="Cancel me")
        await tl.add(task)
        await tl.cancel(task.id)

        t = await tl.get(task.id)
        assert t.status == TaskStatus.CANCELLED

    async def test_reset(self, tl: SharedTaskList):
        task = TaskDefinition(title="T1", description="Retry me")
        await tl.add(task)
        await tl.claim(task.id, "w1")
        await tl.start(task.id)
        await tl.fail(task.id, error="bad")
        await tl.reset(task.id)

        t = await tl.get(task.id)
        assert t.status == TaskStatus.PENDING
        assert t.assigned_to is None

    async def test_get_by_member(self, tl: SharedTaskList):
        t1 = TaskDefinition(title="T1", description="A")
        t2 = TaskDefinition(title="T2", description="B")
        await tl.add(t1)
        await tl.add(t2)
        await tl.claim(t1.id, "w1")
        await tl.claim(t2.id, "w2")

        tasks = await tl.get_by_member("w1")
        assert len(tasks) == 1
        assert tasks[0].id == t1.id

    async def test_get_by_status(self, tl: SharedTaskList):
        t1 = TaskDefinition(title="T1", description="A")
        t2 = TaskDefinition(title="T2", description="B")
        await tl.add(t1)
        await tl.add(t2)
        await tl.claim(t1.id, "w1")

        pending = await tl.get_by_status(TaskStatus.PENDING)
        assert len(pending) == 1
        assert pending[0].id == t2.id

    async def test_stats(self, tl: SharedTaskList):
        t1 = TaskDefinition(title="T1", description="A")
        t2 = TaskDefinition(title="T2", description="B")
        await tl.add(t1)
        await tl.add(t2)
        await tl.claim(t1.id, "w1")
        await tl.start(t1.id)
        await tl.complete(t1.id, result="ok")

        stats = await tl.stats()
        assert stats["total"] == 2
        assert stats["completed"] == 1
        assert stats["pending"] == 1

    async def test_priority_ordering(self, tl: SharedTaskList):
        low = TaskDefinition(title="Low", description="L", priority=TaskPriority.LOW)
        critical = TaskDefinition(
            title="Critical", description="C", priority=TaskPriority.CRITICAL
        )
        await tl.add(low)
        await tl.add(critical)

        available = await tl.get_available()
        assert available[0].id == critical.id  # higher priority first


class TestArtifactStore:
    """Test the in-memory artifact store."""

    TEAM_ID = "team-1"

    @pytest.fixture
    def store(self) -> InMemoryArtifactStore:
        return InMemoryArtifactStore()

    async def test_store_and_get(self, store: InMemoryArtifactStore):
        artifact = Artifact(
            name="report.md",
            content="# Report\nAll good.",
            content_type="text/markdown",
        )
        await store.store(self.TEAM_ID, artifact)
        retrieved = await store.get(self.TEAM_ID, artifact.id)
        assert retrieved is not None
        assert retrieved.name == "report.md"

    async def test_get_nonexistent(self, store: InMemoryArtifactStore):
        result = await store.get(self.TEAM_ID, "nope")
        assert result is None

    async def test_list_artifacts(self, store: InMemoryArtifactStore):
        a1 = Artifact(name="a.txt", content="a")
        a2 = Artifact(name="b.txt", content="b")
        await store.store(self.TEAM_ID, a1)
        await store.store(self.TEAM_ID, a2)

        artifacts = await store.list(self.TEAM_ID)
        assert len(artifacts) == 2

    async def test_list_empty_team(self, store: InMemoryArtifactStore):
        artifacts = await store.list("empty-team")
        assert artifacts == []

    async def test_delete(self, store: InMemoryArtifactStore):
        a = Artifact(name="del.txt", content="x")
        await store.store(self.TEAM_ID, a)
        deleted = await store.delete(self.TEAM_ID, a.id)
        assert deleted is True
        assert await store.get(self.TEAM_ID, a.id) is None

    async def test_delete_nonexistent(self, store: InMemoryArtifactStore):
        assert await store.delete(self.TEAM_ID, "nothing") is False

    async def test_clear(self, store: InMemoryArtifactStore):
        for i in range(5):
            await store.store(self.TEAM_ID, Artifact(name=f"f{i}", content="x"))
        await store.clear(self.TEAM_ID)
        assert len(await store.list(self.TEAM_ID)) == 0
