# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Pytest configuration and fixtures for agent-teams tests."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_teams.manager import MemberRunFactory, TeamManager
from agent_teams.protocol.channel import InMemoryChannel
from agent_teams.state.artifact_store import InMemoryArtifactStore
from agent_teams.state.task_list import SharedTaskList
from agent_teams.types import (
    AgentMemberConfig,
    ExecutionMode,
    SupervisorConfig,
    TaskDefinition,
    TeamConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_echo_factory() -> MemberRunFactory:
    """Return a factory that creates echo agents (reply = prompt)."""

    def factory(config: AgentMemberConfig):
        async def run(prompt: str, context: dict[str, Any] | None = None) -> str:
            return f"[{config.name}] {prompt}"

        return run

    return factory


def make_failing_factory(*, fail_times: int = 1) -> MemberRunFactory:
    """Return a factory whose agents fail *fail_times* then succeed."""
    call_counts: dict[str, int] = {}

    def factory(config: AgentMemberConfig):
        call_counts[config.id] = 0

        async def run(prompt: str, context: dict[str, Any] | None = None) -> str:
            call_counts[config.id] += 1
            if call_counts[config.id] <= fail_times:
                raise RuntimeError(f"Simulated failure #{call_counts[config.id]}")
            return f"[{config.name}] recovered: {prompt}"

        return run

    return factory


def make_slow_factory(*, delay: float = 2.0) -> MemberRunFactory:
    """Return a factory whose agents sleep before answering."""

    def factory(config: AgentMemberConfig):
        async def run(prompt: str, context: dict[str, Any] | None = None) -> str:
            await asyncio.sleep(delay)
            return f"[{config.name}] (slow) {prompt}"

        return run

    return factory


# ---------------------------------------------------------------------------
# Configs
# ---------------------------------------------------------------------------


def make_member(
    name: str = "agent-1",
    role: str = "worker",
    **overrides: Any,
) -> AgentMemberConfig:
    """Create a test member config."""
    return AgentMemberConfig(
        name=name,
        role=role,
        **overrides,
    )


def make_team_config(
    name: str = "test-team",
    execution_mode: ExecutionMode = ExecutionMode.SUPERVISOR,
    members: list[AgentMemberConfig] | None = None,
    **overrides: Any,
) -> TeamConfig:
    """Create a test team config."""
    if members is None:
        members = [
            make_member("planner", "planner"),
            make_member("coder", "coder"),
            make_member("reviewer", "reviewer"),
        ]
    return TeamConfig(
        name=name,
        execution_mode=execution_mode,
        members=members,
        supervisor=SupervisorConfig(name=members[0].name) if execution_mode == ExecutionMode.SUPERVISOR else SupervisorConfig(),
        **overrides,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def channel() -> InMemoryChannel:
    return InMemoryChannel()


@pytest.fixture
def artifact_store() -> InMemoryArtifactStore:
    return InMemoryArtifactStore()


@pytest.fixture
def task_list() -> SharedTaskList:
    return SharedTaskList()


@pytest.fixture
def echo_factory() -> MemberRunFactory:
    return make_echo_factory()


@pytest.fixture
def manager(echo_factory: MemberRunFactory) -> TeamManager:
    return TeamManager(member_run_factory=echo_factory)


@pytest.fixture
def team_config() -> TeamConfig:
    return make_team_config()
