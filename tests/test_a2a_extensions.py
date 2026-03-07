# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Tests for agent_teams.a2a.extensions module."""

from __future__ import annotations

import pytest

from agent_teams.a2a.extensions import (
    TEAM_COORDINATION_URI,
    agent_extension,
    extract_team_metadata,
    is_team_extension_active,
    make_health_metadata,
    make_reaction_metadata,
    make_task_metadata,
)


class TestConstants:
    def test_uri(self):
        assert TEAM_COORDINATION_URI == "https://datalayer.io/ext/team-coordination/v1"


class TestAgentExtension:
    def test_basic(self):
        ext = agent_extension()
        assert ext["uri"] == TEAM_COORDINATION_URI
        assert "description" in ext
        assert ext["required"] is False
        assert "params" not in ext

    def test_required(self):
        ext = agent_extension(required=True)
        assert ext["required"] is True

    def test_with_params(self):
        ext = agent_extension(params={"max_members": 10})
        assert ext["params"]["max_members"] == 10


class TestMakeTaskMetadata:
    def test_minimal(self):
        md = make_task_metadata(team_id="team-1")
        assert md["x-datalayer-team.team-id"] == "team-1"
        assert "x-datalayer-team.assigned-to" not in md

    def test_full(self):
        md = make_task_metadata(
            team_id="t1",
            assigned_to="m1",
            priority=2,
            depends_on=["t0"],
            execution_mode="supervisor",
            role_hint="analyst",
        )
        assert md["x-datalayer-team.team-id"] == "t1"
        assert md["x-datalayer-team.assigned-to"] == "m1"
        assert md["x-datalayer-team.priority"] == 2
        assert md["x-datalayer-team.depends-on"] == ["t0"]
        assert md["x-datalayer-team.execution-mode"] == "supervisor"
        assert md["x-datalayer-team.role-hint"] == "analyst"


class TestMakeHealthMetadata:
    def test_basic(self):
        md = make_health_metadata(member_id="m1", state="healthy")
        assert md["x-datalayer-team.health.member-id"] == "m1"
        assert md["x-datalayer-team.health.state"] == "healthy"
        assert md["x-datalayer-team.health.failures"] == 0

    def test_with_heartbeat(self):
        md = make_health_metadata(
            member_id="m1",
            state="stale",
            last_heartbeat="2025-01-01T00:00:00Z",
            consecutive_failures=3,
        )
        assert md["x-datalayer-team.health.last-heartbeat"] == "2025-01-01T00:00:00Z"
        assert md["x-datalayer-team.health.failures"] == 3


class TestMakeReactionMetadata:
    def test_basic(self):
        md = make_reaction_metadata(
            trigger="task-failed",
            action="notify",
            target_id="t1",
        )
        assert md["x-datalayer-team.reaction.trigger"] == "task-failed"
        assert md["x-datalayer-team.reaction.action"] == "notify"
        assert md["x-datalayer-team.reaction.target-id"] == "t1"
        assert md["x-datalayer-team.reaction.attempt"] == 1
        assert md["x-datalayer-team.reaction.escalated"] is False

    def test_escalated(self):
        md = make_reaction_metadata(
            trigger="member-dead",
            action="restart-member",
            target_id="m2",
            attempt=5,
            escalated=True,
        )
        assert md["x-datalayer-team.reaction.attempt"] == 5
        assert md["x-datalayer-team.reaction.escalated"] is True


class TestExtractTeamMetadata:
    def test_extract(self):
        full = {
            "x-datalayer-team.team-id": "t1",
            "x-datalayer-team.assigned-to": "m1",
            "other-key": "other-value",
        }
        extracted = extract_team_metadata(full)
        assert len(extracted) == 2
        assert "other-key" not in extracted

    def test_empty(self):
        assert extract_team_metadata({}) == {}


class TestIsTeamExtensionActive:
    def test_active(self):
        assert is_team_extension_active([TEAM_COORDINATION_URI]) is True

    def test_inactive(self):
        assert is_team_extension_active([]) is False
        assert is_team_extension_active(["https://other.ext/v1"]) is False

    def test_multiple(self):
        assert is_team_extension_active([
            "https://other.ext/v1",
            TEAM_COORDINATION_URI,
        ]) is True
