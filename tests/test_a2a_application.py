# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Tests for the A2A application layer (A2ATeamApp, create_a2a_team_app)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from agent_teams.a2a.application import (
    A2ATeamApp,
    _build_skills_from_config,
    create_a2a_team_app,
)
from agent_teams.manager import TeamManager
from agent_teams.types import (
    AgentMemberConfig,
    ExecutionMode,
    SupervisorConfig,
    TeamConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    name: str = "test-team",
    members: list[AgentMemberConfig] | None = None,
) -> TeamConfig:
    if members is None:
        members = [
            AgentMemberConfig(id="m1", name="Planner", role="planner", goal="Plan things"),
            AgentMemberConfig(id="m2", name="Coder", role="coder", tools=["python"]),
        ]
    return TeamConfig(
        name=name,
        description="A test team",
        members=members,
        execution_mode=ExecutionMode.SUPERVISOR,
        supervisor=SupervisorConfig(name=members[0].name),
    )


# ---------------------------------------------------------------------------
# _build_skills_from_config
# ---------------------------------------------------------------------------


class TestBuildSkills:
    """Tests for skill generation from team config."""

    def test_skills_from_members(self) -> None:
        config = _make_config()
        skills = _build_skills_from_config(config)

        assert len(skills) == 2

        s1 = skills[0]
        assert s1["id"] == "m1"
        assert s1["name"] == "Planner"
        assert s1["description"] == "Plan things"
        assert "planner" in s1["tags"]

        s2 = skills[1]
        assert s2["id"] == "m2"
        assert s2["name"] == "Coder"
        assert "python" in s2["tags"]

    def test_skill_default_description(self) -> None:
        member = AgentMemberConfig(id="m3", name="Reviewer", role="reviewer")
        config = _make_config(members=[member])
        skills = _build_skills_from_config(config)

        assert "reviewer: Reviewer" in skills[0]["description"]

    def test_empty_members(self) -> None:
        config = TeamConfig(
            name="empty-team",
            description="No members",
            members=[],
            execution_mode=ExecutionMode.SUPERVISOR,
            supervisor=SupervisorConfig(),
        )
        skills = _build_skills_from_config(config)
        assert skills == []


# ---------------------------------------------------------------------------
# A2ATeamApp construction (with mocked manager)
# ---------------------------------------------------------------------------


class TestA2ATeamApp:
    """Tests for A2ATeamApp initialization."""

    def _mock_manager(self, config: TeamConfig, with_context: bool = False) -> TeamManager:
        """Create a mock manager that returns the given config."""
        manager = MagicMock(spec=TeamManager)

        team_obj = MagicMock()
        team_obj.config = config
        manager.get_team.return_value = team_obj

        if with_context:
            ctx = MagicMock()
            ctx.task_list = MagicMock()
            ctx.artifact_store = MagicMock()
            ctx.members = {}
            manager._contexts = {config.id: ctx}
        else:
            manager._contexts = {}

        return manager

    def test_creates_fasta2a_app(self) -> None:
        config = _make_config()
        manager = self._mock_manager(config)

        team_app = A2ATeamApp(manager, config.id)

        assert team_app.app is not None
        assert team_app.broker is not None
        assert team_app.worker is not None
        assert team_app.storage is not None

    def test_creates_with_context(self) -> None:
        config = _make_config()
        manager = self._mock_manager(config, with_context=True)

        team_app = A2ATeamApp(manager, config.id)

        # Storage should be TeamTaskStorage wrapped in StreamingStorageWrapper
        assert team_app.storage is not None

    def test_creates_without_streaming(self) -> None:
        config = _make_config()
        manager = self._mock_manager(config)

        team_app = A2ATeamApp(
            manager, config.id, enable_streaming=False
        )
        assert team_app.storage is not None

    def test_update_members(self) -> None:
        config = _make_config()
        manager = self._mock_manager(config)

        team_app = A2ATeamApp(manager, config.id)
        mock_member = MagicMock()
        team_app.update_members({"m1": mock_member})

        assert team_app.worker._members == {"m1": mock_member}


class TestCreateA2ATeamApp:
    """Tests for the create_a2a_team_app factory."""

    def test_returns_fasta2a_app(self) -> None:
        config = _make_config()
        manager = MagicMock(spec=TeamManager)

        team_obj = MagicMock()
        team_obj.config = config
        manager.get_team.return_value = team_obj
        manager._contexts = {}

        app = create_a2a_team_app(manager, config.id)

        # Should be a FastA2A instance (Starlette app)
        assert hasattr(app, "state")
        assert hasattr(app.state, "team_app")
        assert isinstance(app.state.team_app, A2ATeamApp)

    def test_custom_agent_url(self) -> None:
        config = _make_config()
        manager = MagicMock(spec=TeamManager)

        team_obj = MagicMock()
        team_obj.config = config
        manager.get_team.return_value = team_obj
        manager._contexts = {}

        app = create_a2a_team_app(
            manager,
            config.id,
            agent_url="https://myteam.example.com",
        )
        assert app.state.team_app is not None
