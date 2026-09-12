# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Expose an agent team as a fasta2a A2A endpoint.

The ``A2ATeamApp`` wraps a ``TeamManager`` and a specific team into a
fasta2a ``FastA2A`` application, making the entire team accessible via
the A2A protocol:

- ``GET /.well-known/agent-card.json`` — team's agent card
- ``POST /`` (method: ``message/send``) — send a task to the team
- ``POST /`` (method: ``message/stream``) — stream task execution via SSE
- ``POST /`` (method: ``tasks/get``) — get task status
- ``POST /`` (method: ``tasks/cancel``) — cancel a task

The team appears to external callers as a single agent. Internally,
the task is routed through the team's orchestrator to the appropriate
member(s).

Usage::

    from agent_teams.a2a import create_a2a_team_app

    manager = TeamManager(...)
    team_id = await manager.create_team(config)
    await manager.start_team(team_id)

    # Mount as a sub-application
    a2a_app = create_a2a_team_app(manager, team_id)
    main_app.mount("/a2a/teams/my-team", a2a_app)
"""

from __future__ import annotations

import logging
from typing import Any

from fasta2a import FastA2A, Skill
from fasta2a.broker import InMemoryBroker
from fasta2a.storage import InMemoryStorage

from ..manager import TeamManager
from ..types import TeamConfig
from .extensions import agent_extension
from .storage import TeamTaskStorage
from .worker import TeamMemberWorker

logger = logging.getLogger(__name__)


class A2ATeamApp:
    """Wraps a team as a fasta2a FastA2A application.

    This class manages the lifecycle of the fasta2a components
    (broker, worker, storage) for a specific team.

    Attributes
    ----------
    app:
        The underlying ``FastA2A`` Starlette application.
    worker:
        The ``TeamMemberWorker`` that executes tasks.
    storage:
        The ``TeamTaskStorage`` or plain ``InMemoryStorage``.
    broker:
        The fasta2a ``InMemoryBroker``.
    """

    def __init__(
        self,
        manager: TeamManager,
        team_id: str,
        *,
        agent_url: str = "http://localhost:8000",
        enable_streaming: bool = True,
    ) -> None:
        self._manager = manager
        self._team_id = team_id

        team = manager.get_team(team_id)
        config = team.config

        # Create fasta2a components
        self.broker = InMemoryBroker()

        # Build storage that bridges to agent-teams state
        ctx = manager._contexts.get(team_id)
        if ctx:
            base_storage = TeamTaskStorage(
                task_list=ctx.task_list,
                artifact_store=ctx.artifact_store,
                team_id=team_id,
            )
        else:
            # Team not yet started — use plain in-memory storage
            base_storage = InMemoryStorage()

        # `StreamingStorageWrapper` no longer exists in fasta2a: current
        # `FastA2A` handles `message/stream` natively, through its own
        # `TaskManager`, on whatever storage it is given — a wrapper class
        # was never needed for streaming to work. `enable_streaming` is kept
        # for API compatibility; it no longer changes what storage is built.
        del enable_streaming
        self.storage = base_storage

        # Create worker with current team members
        members = ctx.members if ctx else {}
        self.worker = TeamMemberWorker(
            broker=self.broker,
            storage=self.storage,
            members=members,
        )

        # Build skills from team members
        skills = _build_skills_from_config(config)

        # Build A2A extension declaration
        team_ext = agent_extension()

        # Create the FastA2A application
        self.app = FastA2A(
            storage=self.storage,
            broker=self.broker,
            name=config.name,
            description=config.description or f"Agent team: {config.name}",
            url=agent_url,
            version="0.1.0",
            skills=skills,
            extensions=[team_ext],
            streaming=enable_streaming,
        )

    def update_members(self, members: dict[str, Any]) -> None:
        """Update the worker's member map (e.g., after team restart)."""
        self.worker.set_members(members)


def create_a2a_team_app(
    manager: TeamManager,
    team_id: str,
    *,
    agent_url: str = "http://localhost:8000",
    enable_streaming: bool = True,
) -> FastA2A:
    """Create a fasta2a FastA2A app that exposes a team as an A2A agent.

    Parameters
    ----------
    manager:
        The ``TeamManager`` managing the team.
    team_id:
        The ID of the team to expose.
    agent_url:
        The URL at which this A2A endpoint will be accessible.
    enable_streaming:
        Whether to enable SSE streaming for ``message/stream``.

    Returns
    -------
    FastA2A
        A Starlette application implementing the A2A protocol.
    """
    team_app = A2ATeamApp(
        manager,
        team_id,
        agent_url=agent_url,
        enable_streaming=enable_streaming,
    )

    # Store the A2ATeamApp instance on the FastA2A app for later access
    team_app.app.state.team_app = team_app
    return team_app.app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_skills_from_config(config: TeamConfig) -> list[Skill]:
    """Build A2A skills from team member configurations.

    Each member becomes a skill, describing what they can do.
    """
    skills: list[Skill] = []
    for member in config.members:
        skill = Skill(
            id=member.id,
            name=member.name,
            description=member.goal or f"{member.role}: {member.name}",
            tags=[member.role] + member.tools,
            input_modes=["text/plain"],
            output_modes=["text/plain"],
        )
        skills.append(skill)
    return skills
