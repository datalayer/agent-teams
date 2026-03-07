# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Tests for core types and Pydantic models."""

from __future__ import annotations

import pytest

from agent_teams.types import (
    AgentMemberConfig,
    AgentMemberState,
    ApprovalPolicy,
    Artifact,
    AssignTaskRequest,
    CreateTeamRequest,
    EventType,
    ExecutionMode,
    MemberStatus,
    NotificationConfig,
    OrchestrationProtocol,
    OutputConfig,
    SupervisorConfig,
    TaskDefinition,
    TaskPriority,
    TaskResult,
    TaskStatus,
    TeamConfig,
    TeamEvent,
    TeamMetrics,
    TeamState,
    TeamStatus,
    TeamSummary,
    UpdateTeamRequest,
    ValidationConfig,
)


class TestEnums:
    """Verify enum members and string serialization."""

    def test_team_status_values(self):
        assert TeamStatus.DRAFT.value == "draft"
        assert TeamStatus.RUNNING.value == "running"
        assert TeamStatus.COMPLETED.value == "completed"

    def test_execution_mode_values(self):
        assert ExecutionMode.SUPERVISOR.value == "supervisor"
        assert ExecutionMode.GRAPH.value == "graph"

    def test_task_priority_ordering(self):
        assert TaskPriority.CRITICAL < TaskPriority.LOW

    def test_event_type_categories(self):
        """Ensure we have event types for teams, tasks, and members."""
        values = [e.value for e in EventType]
        assert any("team" in v for v in values)
        assert any("task" in v for v in values)
        assert any("member" in v for v in values)


class TestAgentMemberConfig:
    """Tests for agent member configuration."""

    def test_minimal_member(self):
        m = AgentMemberConfig(name="coder", role="coder")
        assert m.name == "coder"
        assert m.role == "coder"
        assert m.id  # auto-generated UUID
        assert m.approval == ApprovalPolicy.AUTO
        assert m.max_concurrent_tasks == 1

    def test_full_member(self):
        m = AgentMemberConfig(
            name="planner",
            role="planner",
            goal="Plan tasks",
            model="anthropic:claude-sonnet-4-5",
            tools=["read_file", "write_file"],
            system_prompt="You are a planner.",
            max_concurrent_tasks=3,
        )
        assert m.model == "anthropic:claude-sonnet-4-5"
        assert len(m.tools) == 2

    def test_member_serialization(self):
        m = AgentMemberConfig(name="test", role="worker")
        data = m.model_dump()
        assert "name" in data
        assert "id" in data
        restored = AgentMemberConfig.model_validate(data)
        assert restored.name == m.name
        assert restored.id == m.id


class TestTeamConfig:
    """Tests for team configuration."""

    def test_minimal_team(self):
        members = [AgentMemberConfig(name="a", role="worker")]
        tc = TeamConfig(name="my-team", members=members)
        assert tc.execution_mode == ExecutionMode.SUPERVISOR  # default
        assert len(tc.members) == 1

    def test_team_with_supervisor(self):
        sup = AgentMemberConfig(name="boss", role="supervisor")
        worker = AgentMemberConfig(name="worker", role="worker")
        tc = TeamConfig(
            name="supervised-team",
            members=[sup, worker],
            supervisor=SupervisorConfig(name="Boss Supervisor", model="gpt-4"),
        )
        assert tc.supervisor is not None
        assert tc.supervisor.name == "Boss Supervisor"

    def test_validation_config(self):
        vc = ValidationConfig(timeout_seconds=120, max_retries=5)
        assert vc.timeout_seconds == 120
        assert vc.heartbeat_interval_seconds == 30  # default

    def test_notification_config(self):
        nc = NotificationConfig(slack="#alerts", email="team@example.com")
        assert nc.slack == "#alerts"
        assert nc.email == "team@example.com"

    def test_output_config(self):
        oc = OutputConfig(formats=["JSON", "markdown"], storage="s3://bucket")
        assert "markdown" in oc.formats
        assert oc.storage == "s3://bucket"


class TestTaskDefinition:
    """Tests for task definitions."""

    def test_minimal_task(self):
        t = TaskDefinition(title="Do stuff", description="Please do it")
        assert t.status == TaskStatus.PENDING
        assert t.priority == TaskPriority.MEDIUM

    def test_task_with_dependencies(self):
        t1 = TaskDefinition(title="First", description="First task")
        t2 = TaskDefinition(
            title="Second",
            description="Depends on first",
            depends_on=[t1.id],
        )
        assert t1.id in t2.depends_on


class TestTeamState:
    """Tests for runtime team state."""

    def test_initial_state(self):
        members = [AgentMemberConfig(name="a", role="worker")]
        config = TeamConfig(name="test", members=members)
        state = TeamState(id=config.id, config=config)
        assert state.status == TeamStatus.DRAFT
        assert state.events == []
        assert state.tasks == []

    def test_state_serialization_roundtrip(self):
        members = [AgentMemberConfig(name="a", role="worker")]
        config = TeamConfig(name="test", members=members)
        state = TeamState(id=config.id, config=config)
        data = state.model_dump(mode="json")
        restored = TeamState.model_validate(data)
        assert restored.config.name == "test"

    def test_team_summary(self):
        members = [AgentMemberConfig(name="a", role="worker")]
        config = TeamConfig(name="summary-team", members=members)
        state = TeamState(id=config.id, config=config, status=TeamStatus.RUNNING)
        summary = TeamSummary(
            id=config.id,
            name=config.name,
            status=state.status,
            member_count=len(config.members),
            execution_mode=config.execution_mode,
        )
        assert summary.name == "summary-team"
        assert summary.member_count == 1


class TestAPIModels:
    """Tests for request/response API models."""

    def test_create_team_request(self):
        members = [AgentMemberConfig(name="a", role="worker")]
        config = TeamConfig(name="new", members=members)
        req = CreateTeamRequest(config=config)
        assert req.config.name == "new"

    def test_assign_task_request(self):
        req = AssignTaskRequest(
            title="Analyze data",
            description="Run analysis pipeline",
        )
        assert req.title == "Analyze data"

    def test_update_team_request(self):
        req = UpdateTeamRequest(name="updated-name")
        assert req.name == "updated-name"
        assert req.execution_mode is None  # optional

    def test_team_metrics(self):
        m = TeamMetrics(
            run_status="running",
            tasks_completed=7,
            tasks_failed=1,
            tasks_pending=2,
            agents_active=3,
            agents_total=5,
        )
        assert m.tasks_completed == 7
        assert m.agents_total == 5

    def test_team_metrics_defaults(self):
        m = TeamMetrics()
        assert m.run_status == "Unknown"
        assert m.tasks_completed == 0
