# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Datalayer Agent Teams — orchestrate teams of AI agent runtimes.

Public API
----------

Types & Models
~~~~~~~~~~~~~~
.. autosummary::
    TeamConfig
    TeamState
    TeamStatus
    AgentMemberConfig
    AgentMemberState
    TaskDefinition
    TaskResult
    TaskStatus
    ExecutionMode
    TeamEvent

Protocol
~~~~~~~~
.. autosummary::
    CoordinationMessage
    TeamChannel
    InMemoryChannel

Orchestration
~~~~~~~~~~~~~
.. autosummary::
    BaseOrchestrator
    SupervisorOrchestrator
    SequentialOrchestrator
    ParallelOrchestrator

Manager
~~~~~~~
.. autosummary::
    TeamManager

App
~~~
.. autosummary::
    create_app
"""

from .__version__ import __version__

# ---------------------------------------------------------------------------
# Types & Models
# ---------------------------------------------------------------------------
from .types import (
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

# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------
from .protocol.channel import InMemoryChannel, TeamChannel
from .protocol.messages import (
    CoordinationMessage,
    MemberHeartbeat,
    MemberStatusUpdate,
    MessageType,
    TaskAssignment,
    TaskDelegation,
    TaskResultMessage,
    TeamBroadcast,
    TeamCommand,
)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
from .state.artifact_store import ArtifactStore, InMemoryArtifactStore
from .state.task_list import SharedTaskList

# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
from .orchestration.base import BaseOrchestrator, MemberProxy, OrchestratorContext
from .orchestration.parallel import ParallelOrchestrator
from .orchestration.sequential import SequentialOrchestrator
from .orchestration.supervisor import SupervisorOrchestrator

# ---------------------------------------------------------------------------
# Health, Reactions, Hooks
# ---------------------------------------------------------------------------
from .health import HealthConfig, HealthMonitor, HealthState, MemberHealth
from .hooks import Hook, HookEvent, HookRegistry, HookResult
from .reactions import (
    ReactionAction,
    ReactionConfig,
    ReactionEngine,
    ReactionPriority,
    ReactionState,
    ReactionTrigger,
)

# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------
from .manager import TeamManager

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
from .app import create_app

# ---------------------------------------------------------------------------
# A2A integration (optional — requires fasta2a)
# ---------------------------------------------------------------------------
try:
    from .a2a import (
        A2AChannel,
        A2ATeamApp,
        CompositeChannel,
        TEAM_COORDINATION_URI,
        TeamMemberWorker,
        TeamTaskStorage,
        agent_extension,
        create_a2a_team_app,
        extract_team_metadata,
        is_team_extension_active,
        make_health_metadata,
        make_reaction_metadata,
        make_task_metadata,
    )

    _A2A_EXPORTS = [
        "A2AChannel",
        "A2ATeamApp",
        "CompositeChannel",
        "TEAM_COORDINATION_URI",
        "TeamMemberWorker",
        "TeamTaskStorage",
        "agent_extension",
        "create_a2a_team_app",
        "extract_team_metadata",
        "is_team_extension_active",
        "make_health_metadata",
        "make_reaction_metadata",
        "make_task_metadata",
    ]
except ImportError:
    _A2A_EXPORTS = []

__all__ = [
    "__version__",
    # Types
    "AgentMemberConfig",
    "AgentMemberState",
    "ApprovalPolicy",
    "Artifact",
    "AssignTaskRequest",
    "CreateTeamRequest",
    "EventType",
    "ExecutionMode",
    "MemberStatus",
    "NotificationConfig",
    "OrchestrationProtocol",
    "OutputConfig",
    "SupervisorConfig",
    "TaskDefinition",
    "TaskPriority",
    "TaskResult",
    "TaskStatus",
    "TeamConfig",
    "TeamEvent",
    "TeamMetrics",
    "TeamState",
    "TeamStatus",
    "TeamSummary",
    "UpdateTeamRequest",
    "ValidationConfig",
    # Protocol
    "CoordinationMessage",
    "InMemoryChannel",
    "MemberHeartbeat",
    "MemberStatusUpdate",
    "MessageType",
    "TaskAssignment",
    "TaskDelegation",
    "TaskResultMessage",
    "TeamBroadcast",
    "TeamChannel",
    "TeamCommand",
    # State
    "ArtifactStore",
    "InMemoryArtifactStore",
    "SharedTaskList",
    # Orchestration
    "BaseOrchestrator",
    "MemberProxy",
    "OrchestratorContext",
    "ParallelOrchestrator",
    "SequentialOrchestrator",
    "SupervisorOrchestrator",
    # Health, Reactions, Hooks
    "HealthConfig",
    "HealthMonitor",
    "HealthState",
    "Hook",
    "HookEvent",
    "HookRegistry",
    "HookResult",
    "MemberHealth",
    "ReactionAction",
    "ReactionConfig",
    "ReactionEngine",
    "ReactionPriority",
    "ReactionState",
    "ReactionTrigger",
    # Manager
    "TeamManager",
    # App
    "create_app",
    # A2A (conditional)
    *_A2A_EXPORTS,
]
