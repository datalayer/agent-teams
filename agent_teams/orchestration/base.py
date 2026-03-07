# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Base orchestrator interface and supporting types.

An orchestrator is responsible for:
1. Deciding which member should execute a task.
2. Dispatching tasks to members via the coordination channel.
3. Handling task results, retries, and escalation.
4. Coordinating the overall execution flow of the team.

Inspired by:
- Gastown's Mayor/Witness supervisor pattern
- Agent-orchestrator's LifecycleManager + reaction engine
- Pydantic-AI's graph-based control flow
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from ..protocol.channel import TeamChannel
from ..state.artifact_store import ArtifactStore
from ..state.task_list import SharedTaskList
from ..types import (
    AgentMemberConfig,
    AgentMemberState,
    MemberStatus,
    TaskDefinition,
    TaskResult,
    TeamConfig,
    TeamEvent,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Member proxy — abstraction over a remote or in-process agent
# ---------------------------------------------------------------------------


@dataclass
class MemberProxy:
    """Proxy to an agent member, whether in-process or remote.

    The orchestrator uses this proxy to send prompts and receive
    responses without caring about the transport layer.
    """

    config: AgentMemberConfig
    state: AgentMemberState
    _run_fn: Optional[Any] = field(default=None, repr=False)

    @property
    def id(self) -> str:
        return self.config.id

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def status(self) -> MemberStatus:
        return self.state.status

    async def run(self, prompt: str, context: dict[str, Any] | None = None) -> str:
        """Execute a prompt on the member agent.

        This delegates to the actual agent runtime — either via
        an in-process adapter or an HTTP call to a remote agent.
        """
        if self._run_fn is None:
            raise RuntimeError(f"Member {self.id} has no run function attached")
        return await self._run_fn(prompt, context or {})


# ---------------------------------------------------------------------------
# Orchestrator context — everything the orchestrator needs
# ---------------------------------------------------------------------------


@dataclass
class OrchestratorContext:
    """Shared context passed to orchestration strategies."""

    config: TeamConfig
    channel: TeamChannel
    task_list: SharedTaskList
    artifact_store: ArtifactStore
    members: dict[str, MemberProxy] = field(default_factory=dict)
    events: list[TeamEvent] = field(default_factory=list)

    def get_member(self, member_id: str) -> Optional[MemberProxy]:
        return self.members.get(member_id)

    def get_idle_members(self) -> list[MemberProxy]:
        return [m for m in self.members.values() if m.status == MemberStatus.IDLE]

    def get_active_members(self) -> list[MemberProxy]:
        return [
            m for m in self.members.values()
            if m.status in (MemberStatus.RUNNING, MemberStatus.WAITING)
        ]


# ---------------------------------------------------------------------------
# Base orchestrator
# ---------------------------------------------------------------------------


class BaseOrchestrator(ABC):
    """Abstract base class for orchestration strategies.

    Subclasses implement the specific coordination pattern.
    The ``TeamManager`` drives the orchestrator by calling:

    1. ``initialize()`` — set up the orchestrator with context.
    2. ``execute(tasks)`` — run the orchestration loop.
    3. ``handle_result(result)`` — process incoming task results.
    4. ``shutdown()`` — clean up.
    """

    def __init__(self) -> None:
        self._ctx: Optional[OrchestratorContext] = None

    @property
    def ctx(self) -> OrchestratorContext:
        if self._ctx is None:
            raise RuntimeError("Orchestrator not initialized")
        return self._ctx

    async def initialize(self, ctx: OrchestratorContext) -> None:
        """Initialize the orchestrator with the team context."""
        self._ctx = ctx
        await self._setup()

    async def _setup(self) -> None:
        """Subclass hook for custom initialization."""

    @abstractmethod
    async def execute(self, tasks: list[TaskDefinition]) -> list[TaskResult]:
        """Execute the given tasks using the configured strategy.

        Returns a list of results (one per task, in completion order).
        """

    @abstractmethod
    async def assign_task(self, task: TaskDefinition) -> bool:
        """Assign a single task to the best-suited member.

        Returns ``True`` if the task was successfully assigned.
        """

    @abstractmethod
    async def handle_result(self, result: TaskResult) -> None:
        """Handle a task result from a member."""

    async def shutdown(self) -> None:
        """Clean up orchestrator resources."""
        self._ctx = None
