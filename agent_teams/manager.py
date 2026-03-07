# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Team Manager — central coordinator for agent teams.

The TeamManager is responsible for the full lifecycle of agent teams:

1. **Create** — Validate configuration, register members.
2. **Start** — Initialize the orchestrator, connect members, begin execution.
3. **Monitor** — Track member heartbeats, task progress, metrics.
4. **Pause/Resume** — Suspend and resume execution.
5. **Stop** — Graceful shutdown with result collection.

It integrates:
- Orchestration strategies (supervisor, sequential, parallel, graph)
- The coordination protocol channel
- Shared state (task list, artifact store)
- Agent runtime connections (in-process or remote)

Inspired by:
- Agent-orchestrator's SessionManager (spawn/restore/kill lifecycle)
- Gastown's Mayor (global coordinator) + Witness (per-project supervisor)
- Pydantic-deepagents' AgentTeam (spawn/assign/broadcast/dissolve)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Coroutine, Optional

from .health import HealthConfig, HealthMonitor, HealthState
from .hooks import HookEvent, HookRegistry, HookResult
from .orchestration.base import BaseOrchestrator, MemberProxy, OrchestratorContext
from .orchestration.parallel import ParallelOrchestrator
from .orchestration.sequential import SequentialOrchestrator
from .orchestration.supervisor import SupervisorOrchestrator
from .protocol.channel import InMemoryChannel, TeamChannel
from .reactions import ReactionEngine, ReactionTrigger
from .state.artifact_store import ArtifactStore, InMemoryArtifactStore
from .state.task_list import SharedTaskList
from .types import (
    AgentMemberConfig,
    AgentMemberState,
    AssignTaskRequest,
    EventType,
    ExecutionMode,
    MemberStatus,
    OrchestrationProtocol,
    TaskDefinition,
    TaskResult,
    TeamConfig,
    TeamEvent,
    TeamMetrics,
    TeamState,
    TeamStatus,
    TeamSummary,
)

# Lazy imports for fasta2a (optional dependency)
_FASTA2A_AVAILABLE = False
try:
    from fasta2a import FastA2A
    from fasta2a.broker import InMemoryBroker
    from fasta2a.storage import InMemoryStorage, StreamingStorageWrapper

    _FASTA2A_AVAILABLE = True
except ImportError:
    pass

logger = logging.getLogger(__name__)

# Type for the function that creates a run_fn for a member
MemberRunFactory = Callable[
    [AgentMemberConfig],
    Callable[[str, dict[str, Any]], Coroutine[Any, Any, str]],
]


class TeamManager:
    """Manages the lifecycle and execution of agent teams.

    Example::

        manager = TeamManager()

        # Create a team
        team_id = await manager.create_team(config)

        # Register a factory that creates run functions for members
        manager.set_member_run_factory(my_factory)

        # Start the team
        await manager.start_team(team_id)

        # Assign work
        await manager.assign_task(team_id, AssignTaskRequest(
            title="Analyze quarterly data",
            description="...",
        ))

        # Monitor
        metrics = manager.get_metrics(team_id)

        # Stop
        await manager.stop_team(team_id)
    """

    def __init__(
        self,
        channel: TeamChannel | None = None,
        artifact_store: ArtifactStore | None = None,
        member_run_factory: MemberRunFactory | None = None,
        health_config: HealthConfig | None = None,
    ) -> None:
        self._teams: dict[str, TeamState] = {}
        self._orchestrators: dict[str, BaseOrchestrator] = {}
        self._contexts: dict[str, OrchestratorContext] = {}
        self._channel = channel or InMemoryChannel()
        self._artifact_store = artifact_store or InMemoryArtifactStore()
        self._member_run_factory = member_run_factory
        self._execution_tasks: dict[str, asyncio.Task] = {}
        # fasta2a A2A components (created per-team when protocol is A2A)
        self._a2a_brokers: dict[str, Any] = {}
        self._a2a_workers: dict[str, Any] = {}
        self._a2a_storages: dict[str, Any] = {}
        # Health monitoring, reaction engine, hooks
        self._health_config = health_config or HealthConfig()
        self._health_monitors: dict[str, HealthMonitor] = {}
        self._reaction_engines: dict[str, ReactionEngine] = {}
        self._hooks = HookRegistry()

    def set_member_run_factory(self, factory: MemberRunFactory) -> None:
        """Set the factory that creates run functions for agent members.

        The factory receives an ``AgentMemberConfig`` and returns an
        async callable ``(prompt, context) -> str`` that executes the
        prompt on the underlying agent runtime.
        """
        self._member_run_factory = factory

    @property
    def hooks(self) -> HookRegistry:
        """Access the hook registry to add lifecycle hooks."""
        return self._hooks

    def get_health_monitor(self, team_id: str) -> HealthMonitor | None:
        """Get the health monitor for a team."""
        return self._health_monitors.get(team_id)

    def get_reaction_engine(self, team_id: str) -> ReactionEngine | None:
        """Get the reaction engine for a team."""
        return self._reaction_engines.get(team_id)

    # ------------------------------------------------------------------
    # Team lifecycle
    # ------------------------------------------------------------------

    async def create_team(self, config: TeamConfig) -> str:
        """Create a new team from configuration.

        Returns the team ID. The team starts in DRAFT status.
        """
        if config.id in self._teams:
            raise ValueError(f"Team {config.id} already exists")

        # Build member states
        member_states = [
            AgentMemberState(
                id=m.id,
                name=m.name,
                role=m.role,
                status=MemberStatus.IDLE,
            )
            for m in config.members
        ]

        team_state = TeamState(
            id=config.id,
            config=config,
            status=TeamStatus.DRAFT,
            members=member_states,
        )

        team_state.events.append(
            TeamEvent(
                type=EventType.TEAM_CREATED,
                source="manager",
                message=f"Team '{config.name}' created with {len(config.members)} members",
            )
        )

        self._teams[config.id] = team_state
        logger.info("Team '%s' (%s) created", config.name, config.id)
        return config.id

    async def start_team(self, team_id: str) -> None:
        """Start a team — initialize members, orchestrator, and begin execution.

        When the team's ``orchestration_protocol`` is ``a2a`` or ``a2a-extended``,
        fasta2a components are initialized to enable A2A protocol communication.
        Remote members with ``agent_endpoint`` set will be contacted via A2A.
        """
        team = self._get_team(team_id)
        if team.status not in (TeamStatus.DRAFT, TeamStatus.STOPPED, TeamStatus.PAUSED):
            raise ValueError(f"Cannot start team in {team.status} state")

        team.status = TeamStatus.STARTING
        team.started_at = datetime.utcnow()

        # Determine if A2A protocol should be used
        use_a2a = (
            team.config.orchestration_protocol
            in (OrchestrationProtocol.A2A, OrchestrationProtocol.A2A_EXTENDED)
            and _FASTA2A_AVAILABLE
        )

        # Set up A2A infrastructure if needed
        if use_a2a:
            await self._setup_a2a_infrastructure(team_id, team.config)

        # Resolve the channel — use composite if any members are remote
        channel = self._resolve_channel(team.config, use_a2a)

        # Create orchestrator based on execution mode
        orchestrator = self._create_orchestrator(team.config.execution_mode)

        # Create member proxies
        members: dict[str, MemberProxy] = {}
        for member_config in team.config.members:
            member_state = self._get_member_state(team, member_config.id)
            if member_state is None:
                member_state = AgentMemberState(
                    id=member_config.id,
                    name=member_config.name,
                    role=member_config.role,
                )

            run_fn = None
            if use_a2a and member_config.agent_endpoint:
                # Remote A2A agent — create A2A client run function
                run_fn = self._create_a2a_run_fn(member_config)
            elif self._member_run_factory:
                run_fn = self._member_run_factory(member_config)

            proxy = MemberProxy(
                config=member_config,
                state=member_state,
                _run_fn=run_fn,
            )
            members[member_config.id] = proxy

            # Register on channel (with URL for remote members)
            if hasattr(channel, 'register'):
                if member_config.agent_endpoint:
                    await channel.register(member_config.id, base_url=member_config.agent_endpoint)
                else:
                    await channel.register(member_config.id)

        # Build orchestrator context
        task_list = SharedTaskList()
        ctx = OrchestratorContext(
            config=team.config,
            channel=channel,
            task_list=task_list,
            artifact_store=self._artifact_store,
            members=members,
            events=team.events,
        )

        # If using A2A, update the worker with team members
        worker = self._a2a_workers.get(team_id)
        if worker is not None:
            worker.set_members(members)

        # Initialize orchestrator
        await orchestrator.initialize(ctx)

        self._orchestrators[team_id] = orchestrator
        self._contexts[team_id] = ctx

        # -- Set up health monitoring -----------------------------------------
        monitor = HealthMonitor(config=self._health_config)
        for mid in members:
            monitor.register(mid)

        # Wire health callbacks to reaction engine
        reactions = ReactionEngine()
        monitor.on_stale = self._make_health_callback(team_id, reactions, ReactionTrigger.MEMBER_UNRESPONSIVE)
        monitor.on_unresponsive = self._make_health_callback(team_id, reactions, ReactionTrigger.MEMBER_UNRESPONSIVE)
        monitor.on_stuck = self._make_health_callback(team_id, reactions, ReactionTrigger.MEMBER_STUCK)
        monitor.on_dead = self._make_health_callback(team_id, reactions, ReactionTrigger.MEMBER_DEAD)
        monitor.on_mass_death = lambda ids: logger.critical(
            "Mass death detected in team %s: %s", team_id, ids
        )

        self._health_monitors[team_id] = monitor
        self._reaction_engines[team_id] = reactions

        # Run lifecycle hook
        hook_result = await self._hooks.run(
            HookEvent.POST_TEAM_START, team_id=team_id, config=team.config
        )

        team.status = TeamStatus.RUNNING

        team.events.append(
            TeamEvent(
                type=EventType.TEAM_STARTED,
                source="manager",
                message=f"Team '{team.config.name}' started with {team.config.execution_mode.value} orchestration",
            )
        )

        logger.info("Team '%s' started", team.config.name)

    async def stop_team(self, team_id: str) -> None:
        """Stop a running team gracefully."""
        team = self._get_team(team_id)
        if team.status not in (TeamStatus.RUNNING, TeamStatus.PAUSED):
            raise ValueError(f"Cannot stop team in {team.status} state")

        await self._hooks.run(HookEvent.PRE_TEAM_STOP, team_id=team_id)

        team.status = TeamStatus.STOPPING

        # Stop health monitor
        monitor = self._health_monitors.pop(team_id, None)
        if monitor:
            await monitor.stop()

        # Remove reaction engine
        self._reaction_engines.pop(team_id, None)

        # Cancel execution task if running
        exec_task = self._execution_tasks.pop(team_id, None)
        if exec_task and not exec_task.done():
            exec_task.cancel()
            try:
                await exec_task
            except asyncio.CancelledError:
                pass

        # Shutdown orchestrator
        orchestrator = self._orchestrators.pop(team_id, None)
        if orchestrator:
            await orchestrator.shutdown()

        # Unregister members from channel
        ctx = self._contexts.pop(team_id, None)
        if ctx:
            for member_id in ctx.members:
                await ctx.channel.unregister(member_id)

        # Clean up A2A resources
        self._a2a_brokers.pop(team_id, None)
        self._a2a_workers.pop(team_id, None)
        self._a2a_storages.pop(team_id, None)

        team.status = TeamStatus.STOPPED
        team.completed_at = datetime.utcnow()

        team.events.append(
            TeamEvent(
                type=EventType.TEAM_STOPPED,
                source="manager",
                message=f"Team '{team.config.name}' stopped",
            )
        )

        await self._hooks.run(HookEvent.POST_TEAM_STOP, team_id=team_id)
        logger.info("Team '%s' stopped", team.config.name)

    async def pause_team(self, team_id: str) -> None:
        """Pause a running team."""
        team = self._get_team(team_id)
        if team.status != TeamStatus.RUNNING:
            raise ValueError(f"Cannot pause team in {team.status} state")

        team.status = TeamStatus.PAUSED
        team.events.append(
            TeamEvent(
                type=EventType.TEAM_PAUSED,
                source="manager",
                message=f"Team '{team.config.name}' paused",
            )
        )

    async def resume_team(self, team_id: str) -> None:
        """Resume a paused team."""
        team = self._get_team(team_id)
        if team.status != TeamStatus.PAUSED:
            raise ValueError(f"Cannot resume team in {team.status} state")

        team.status = TeamStatus.RUNNING
        team.events.append(
            TeamEvent(
                type=EventType.TEAM_RESUMED,
                source="manager",
                message=f"Team '{team.config.name}' resumed",
            )
        )

    async def delete_team(self, team_id: str) -> None:
        """Delete a team (must be stopped or draft)."""
        team = self._get_team(team_id)
        if team.status not in (TeamStatus.DRAFT, TeamStatus.STOPPED, TeamStatus.COMPLETED, TeamStatus.FAILED):
            raise ValueError(f"Cannot delete team in {team.status} state — stop it first")

        self._teams.pop(team_id, None)
        self._orchestrators.pop(team_id, None)
        self._contexts.pop(team_id, None)
        logger.info("Team '%s' deleted", team.config.name)

    # ------------------------------------------------------------------
    # Task management
    # ------------------------------------------------------------------

    async def assign_task(
        self,
        team_id: str,
        request: AssignTaskRequest,
    ) -> TaskDefinition:
        """Assign a new task to the team.

        The orchestrator will route it to the best member.
        """
        team = self._get_team(team_id)
        if team.status != TeamStatus.RUNNING:
            raise ValueError(f"Cannot assign tasks to team in {team.status} state")

        ctx = self._contexts.get(team_id)
        orchestrator = self._orchestrators.get(team_id)
        if ctx is None or orchestrator is None:
            raise RuntimeError(f"Team {team_id} context not initialized")

        task = TaskDefinition(
            title=request.title,
            description=request.description,
            priority=request.priority,
            assigned_to=request.assigned_to,
            input_data=request.input_data,
            depends_on=request.depends_on,
        )

        await ctx.task_list.add(task)

        # Pre-assign hook
        hook_result = await self._hooks.run(
            HookEvent.PRE_TASK_ASSIGN, team_id=team_id, task=task
        )
        if not hook_result.allow:
            logger.info("Hook blocked task assignment: %s", hook_result.reason)
            team.tasks.append(task)
            return task

        # Dispatch immediately via orchestrator
        assigned = await orchestrator.assign_task(task)
        if not assigned:
            logger.warning("Task %s could not be assigned immediately", task.id)

        await self._hooks.run(
            HookEvent.POST_TASK_ASSIGN, team_id=team_id, task=task, assigned=assigned
        )

        team.tasks.append(task)
        return task

    async def execute_tasks(
        self,
        team_id: str,
        tasks: list[TaskDefinition],
    ) -> list[TaskResult]:
        """Execute a batch of tasks through the team's orchestrator.

        This is a blocking call that returns when all tasks complete.
        """
        team = self._get_team(team_id)
        if team.status != TeamStatus.RUNNING:
            raise ValueError(f"Cannot execute tasks on team in {team.status} state")

        orchestrator = self._orchestrators.get(team_id)
        if orchestrator is None:
            raise RuntimeError(f"Team {team_id} orchestrator not initialized")

        results = await orchestrator.execute(tasks)

        # Update team metrics
        for result in results:
            team.total_tokens_used += result.tokens_used

        return results

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list_teams(self) -> list[TeamSummary]:
        """List all managed teams."""
        summaries = []
        for team in self._teams.values():
            active_tasks = len([
                t for t in team.tasks
                if t.status in ("assigned", "in_progress")
            ])
            summaries.append(
                TeamSummary(
                    id=team.id,
                    name=team.config.name,
                    description=team.config.description,
                    owner=team.config.owner,
                    status=team.status,
                    member_count=len(team.config.members),
                    active_tasks=active_tasks,
                    execution_mode=team.config.execution_mode,
                    orchestration_protocol=team.config.orchestration_protocol,
                    created_at=team.started_at,
                )
            )
        return summaries

    def get_team(self, team_id: str) -> TeamState:
        """Get the full state of a team."""
        return self._get_team(team_id)

    def get_metrics(self, team_id: str) -> TeamMetrics:
        """Get metrics for a team."""
        team = self._get_team(team_id)
        ctx = self._contexts.get(team_id)

        duration = 0.0
        if team.started_at:
            end = team.completed_at or datetime.utcnow()
            duration = (end - team.started_at).total_seconds()

        tasks_completed = len([t for t in team.tasks if t.status == "completed"])
        tasks_pending = len([t for t in team.tasks if t.status in ("pending", "assigned")])
        tasks_failed = len([t for t in team.tasks if t.status == "failed"])

        agents_active = 0
        if ctx:
            agents_active = len(ctx.get_active_members())

        return TeamMetrics(
            run_status=team.status.value,
            duration_seconds=duration,
            estimated_cost_usd=team.estimated_cost_usd,
            total_tokens_used=team.total_tokens_used,
            agents_active=agents_active,
            agents_total=len(team.config.members),
            tasks_completed=tasks_completed,
            tasks_pending=tasks_pending,
            tasks_failed=tasks_failed,
            notifications_sent=team.notifications_sent,
        )

    def get_events(
        self,
        team_id: str,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[TeamEvent]:
        """Get recent events for a team."""
        team = self._get_team(team_id)
        events = team.events
        if since:
            events = [e for e in events if e.timestamp > since]
        return events[-limit:]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_team(self, team_id: str) -> TeamState:
        team = self._teams.get(team_id)
        if team is None:
            raise KeyError(f"Team {team_id} not found")
        return team

    def _get_member_state(
        self,
        team: TeamState,
        member_id: str,
    ) -> Optional[AgentMemberState]:
        for member in team.members:
            if member.id == member_id:
                return member
        return None

    def _create_orchestrator(self, mode: ExecutionMode) -> BaseOrchestrator:
        if mode == ExecutionMode.SUPERVISOR:
            return SupervisorOrchestrator()
        elif mode == ExecutionMode.SEQUENTIAL:
            return SequentialOrchestrator()
        elif mode == ExecutionMode.PARALLEL:
            return ParallelOrchestrator()
        elif mode == ExecutionMode.GRAPH:
            # Graph orchestration could use pydantic-graph.
            # For now, fall back to supervisor.
            logger.info("Graph orchestration falling back to supervisor")
            return SupervisorOrchestrator()
        else:
            raise ValueError(f"Unknown execution mode: {mode}")

    # ------------------------------------------------------------------
    # A2A (fasta2a) integration
    # ------------------------------------------------------------------

    def _make_health_callback(
        self,
        team_id: str,
        reactions: ReactionEngine,
        trigger: ReactionTrigger,
    ) -> Callable[[str], None]:
        """Create a callback that fires a reaction when a health event occurs."""
        def _cb(member_id: str) -> None:
            team = self._teams.get(team_id)
            if team:
                team.events.append(
                    TeamEvent(
                        type=EventType.MEMBER_ERROR,
                        source=member_id,
                        message=f"Health event {trigger.value} for member {member_id}",
                    )
                )
                reactions.fire(trigger, member_id)
        return _cb

    def _resolve_channel(
        self,
        config: TeamConfig,
        use_a2a: bool,
    ) -> TeamChannel:
        """Resolve the appropriate channel based on configuration.

        If any members have ``agent_endpoint`` set and A2A is configured,
        create a ``CompositeChannel`` that routes between local/remote.
        Otherwise, use the default channel.
        """
        if not use_a2a:
            return self._channel

        has_remote = any(m.agent_endpoint for m in config.members)
        if not has_remote:
            return self._channel

        # Import A2A channel lazily
        try:
            from .a2a.channel import A2AChannel, CompositeChannel

            remote_channel = A2AChannel()
            return CompositeChannel(local=self._channel, remote=remote_channel)
        except ImportError:
            logger.warning("fasta2a not available, falling back to local channel")
            return self._channel

    async def _setup_a2a_infrastructure(
        self,
        team_id: str,
        config: TeamConfig,
    ) -> None:
        """Set up fasta2a broker, storage, and worker for a team.

        These components enable the team to use A2A protocol for
        task management and streaming.
        """
        if not _FASTA2A_AVAILABLE:
            logger.warning("fasta2a not installed — A2A features disabled for team %s", team_id)
            return

        broker = InMemoryBroker()
        base_storage = InMemoryStorage()
        storage = StreamingStorageWrapper(base_storage, broker)

        self._a2a_brokers[team_id] = broker
        self._a2a_storages[team_id] = storage

        logger.info("A2A infrastructure initialized for team %s", team_id)

    def _create_a2a_run_fn(
        self,
        member_config: AgentMemberConfig,
    ) -> Callable[[str, dict[str, Any]], Coroutine[Any, Any, str]]:
        """Create a run function that sends tasks to a remote A2A agent.

        The function uses fasta2a's ``A2AClient`` to communicate with
        the member's A2A endpoint.
        """
        endpoint = member_config.agent_endpoint

        async def _a2a_run(prompt: str, context: dict[str, Any] | None = None) -> str:
            if not _FASTA2A_AVAILABLE:
                raise RuntimeError("fasta2a not installed — cannot call remote A2A agents")

            from fasta2a.client import A2AClient
            from fasta2a.schema import Message as A2AMessage, TextPart

            import uuid

            client = A2AClient(base_url=endpoint)
            try:
                message = A2AMessage(
                    role="user",
                    kind="message",
                    message_id=str(uuid.uuid4()),
                    parts=[TextPart(kind="text", text=prompt)],
                    metadata=context or {},
                )
                response = await client.send_message(message)

                # Extract result from response
                result = response.get("result")
                if result is None:
                    error = response.get("error", {})
                    raise RuntimeError(
                        f"A2A error from {endpoint}: {error.get('message', 'Unknown error')}"
                    )

                # If result is a Task, extract text from history or artifacts
                if isinstance(result, dict) and result.get("kind") == "task":
                    return _extract_text_from_a2a_task(result)

                # If result is a Message, extract text from parts
                if isinstance(result, dict) and result.get("kind") == "message":
                    return _extract_text_from_parts(result.get("parts", []))

                return str(result)
            finally:
                await client.http_client.aclose()

        return _a2a_run

    def get_a2a_broker(self, team_id: str) -> Any:
        """Get the fasta2a broker for a team (if A2A is enabled)."""
        return self._a2a_brokers.get(team_id)

    def get_a2a_storage(self, team_id: str) -> Any:
        """Get the fasta2a storage for a team (if A2A is enabled)."""
        return self._a2a_storages.get(team_id)


# ---------------------------------------------------------------------------
# Module-level helpers for A2A response parsing
# ---------------------------------------------------------------------------


def _extract_text_from_a2a_task(task: dict[str, Any]) -> str:
    """Extract text output from an A2A Task response."""
    # Try artifacts first
    artifacts = task.get("artifacts", [])
    if artifacts:
        texts = []
        for art in artifacts:
            for part in art.get("parts", []):
                if part.get("kind") == "text":
                    texts.append(part.get("text", ""))
        if texts:
            return "\n".join(texts)

    # Fall back to last agent message in history
    history = task.get("history", [])
    for msg in reversed(history):
        if msg.get("role") == "agent":
            return _extract_text_from_parts(msg.get("parts", []))

    return ""


def _extract_text_from_parts(parts: list[dict[str, Any]]) -> str:
    """Extract text from A2A message parts."""
    texts = []
    for part in parts:
        if part.get("kind") == "text":
            texts.append(part.get("text", ""))
    return "\n".join(texts)
