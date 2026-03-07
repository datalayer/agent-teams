# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Tests for the team manager lifecycle."""

from __future__ import annotations

import pytest

from agent_teams.manager import TeamManager
from agent_teams.types import (
    AssignTaskRequest,
    ExecutionMode,
    TeamConfig,
    TeamStatus,
)

from .conftest import make_echo_factory, make_member, make_team_config


class TestTeamManagerLifecycle:
    """Test create/start/stop/pause/resume/delete lifecycle."""

    @pytest.fixture
    def mgr(self) -> TeamManager:
        return TeamManager(member_run_factory=make_echo_factory())

    @pytest.fixture
    def config(self) -> TeamConfig:
        return make_team_config()

    async def test_create_team(self, mgr: TeamManager, config: TeamConfig):
        team_id = await mgr.create_team(config)
        assert team_id == config.id
        state = mgr.get_team(team_id)
        assert state.status == TeamStatus.DRAFT

    async def test_create_duplicate_raises(self, mgr: TeamManager, config: TeamConfig):
        await mgr.create_team(config)
        with pytest.raises(ValueError, match="already exists"):
            await mgr.create_team(config)

    async def test_start_team(self, mgr: TeamManager, config: TeamConfig):
        team_id = await mgr.create_team(config)
        await mgr.start_team(team_id)
        state = mgr.get_team(team_id)
        assert state.status == TeamStatus.RUNNING

    async def test_start_nonexistent_raises(self, mgr: TeamManager):
        with pytest.raises(KeyError):
            await mgr.start_team("no-such-team")

    async def test_stop_team(self, mgr: TeamManager, config: TeamConfig):
        team_id = await mgr.create_team(config)
        await mgr.start_team(team_id)
        await mgr.stop_team(team_id)
        state = mgr.get_team(team_id)
        assert state.status == TeamStatus.STOPPED

    async def test_pause_and_resume(self, mgr: TeamManager, config: TeamConfig):
        team_id = await mgr.create_team(config)
        await mgr.start_team(team_id)

        await mgr.pause_team(team_id)
        assert mgr.get_team(team_id).status == TeamStatus.PAUSED

        await mgr.resume_team(team_id)
        assert mgr.get_team(team_id).status == TeamStatus.RUNNING

    async def test_delete_stopped_team(self, mgr: TeamManager, config: TeamConfig):
        team_id = await mgr.create_team(config)
        await mgr.delete_team(team_id)
        with pytest.raises(KeyError):
            mgr.get_team(team_id)

    async def test_delete_running_team_raises(
        self, mgr: TeamManager, config: TeamConfig
    ):
        team_id = await mgr.create_team(config)
        await mgr.start_team(team_id)
        with pytest.raises(ValueError, match="stop it first"):
            await mgr.delete_team(team_id)

    async def test_list_teams(self, mgr: TeamManager):
        c1 = make_team_config(name="team-a")
        c2 = make_team_config(name="team-b")
        await mgr.create_team(c1)
        await mgr.create_team(c2)
        summaries = mgr.list_teams()
        assert len(summaries) == 2
        names = {s.name for s in summaries}
        assert "team-a" in names
        assert "team-b" in names


class TestTeamManagerTasks:
    """Test task assignment and execution."""

    @pytest.fixture
    def mgr(self) -> TeamManager:
        return TeamManager(member_run_factory=make_echo_factory())

    async def test_assign_task(self, mgr: TeamManager):
        config = make_team_config()
        team_id = await mgr.create_team(config)
        await mgr.start_team(team_id)

        task = await mgr.assign_task(
            team_id,
            AssignTaskRequest(
                title="Analyze data",
                description="Run the analysis pipeline",
            ),
        )
        assert task.title == "Analyze data"

    async def test_assign_to_stopped_team_raises(self, mgr: TeamManager):
        config = make_team_config()
        team_id = await mgr.create_team(config)
        with pytest.raises(ValueError, match="not running|Cannot assign"):
            await mgr.assign_task(
                team_id,
                AssignTaskRequest(title="T", description="D"),
            )


class TestTeamManagerMetrics:
    """Test metrics and events."""

    @pytest.fixture
    def mgr(self) -> TeamManager:
        return TeamManager(member_run_factory=make_echo_factory())

    async def test_get_metrics(self, mgr: TeamManager):
        config = make_team_config()
        team_id = await mgr.create_team(config)
        metrics = mgr.get_metrics(team_id)
        assert metrics.total_tasks == 0

    async def test_get_events(self, mgr: TeamManager):
        config = make_team_config()
        team_id = await mgr.create_team(config)
        await mgr.start_team(team_id)
        events = mgr.get_events(team_id)
        # Should have at least the team_started event
        assert len(events) >= 1

    async def test_get_events_returns_list(self, mgr: TeamManager):
        config = make_team_config()
        team_id = await mgr.create_team(config)
        await mgr.start_team(team_id)
        all_events = mgr.get_events(team_id)
        # Should have at least team_created and team_started events
        assert len(all_events) >= 2


class TestTeamManagerExecutionModes:
    """Test that different execution modes create the right orchestrator."""

    @pytest.fixture
    def mgr(self) -> TeamManager:
        return TeamManager(member_run_factory=make_echo_factory())

    async def test_sequential_mode(self, mgr: TeamManager):
        config = make_team_config(execution_mode=ExecutionMode.SEQUENTIAL)
        team_id = await mgr.create_team(config)
        await mgr.start_team(team_id)
        assert mgr.get_team(team_id).status == TeamStatus.RUNNING

    async def test_parallel_mode(self, mgr: TeamManager):
        config = make_team_config(execution_mode=ExecutionMode.PARALLEL)
        team_id = await mgr.create_team(config)
        await mgr.start_team(team_id)
        assert mgr.get_team(team_id).status == TeamStatus.RUNNING

    async def test_supervisor_mode(self, mgr: TeamManager):
        config = make_team_config(execution_mode=ExecutionMode.SUPERVISOR)
        team_id = await mgr.create_team(config)
        await mgr.start_team(team_id)
        assert mgr.get_team(team_id).status == TeamStatus.RUNNING
