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
        TEAM_COORDINATION_URI,
        agent_extension,
        extract_team_metadata,
        is_team_extension_active,
        make_health_metadata,
        make_reaction_metadata,
        make_task_metadata,
    )

    _A2A_EXPORTS = [
        "TEAM_COORDINATION_URI",
        "agent_extension",
        "extract_team_metadata",
        "is_team_extension_active",
        "make_health_metadata",
        "make_reaction_metadata",
        "make_task_metadata",
    ]
except ImportError:
    _A2A_EXPORTS = []

# The orchestration extension (ORCHESTRATOR.md O3-01, O3-02) has no legacy
# `fasta2a` message-shape dependency, so it is imported unconditionally
# rather than folded into the guarded block above — a drift in `application`,
# `channel`, `storage` or `worker` must never take this down with it.
from .a2a import (
    ORCHESTRATION_EXTENSION_URI,
    STEER_METHOD,
    Budget,
    ExecutionRef,
    Usage,
    build_delegation_meta,
    error_meta,
    extension_agent_extension,
    is_extension_active,
    paused_meta,
    read_delegation_meta,
    read_usage_meta,
    steer_notification,
    usage_meta,
)

_ORCHESTRATION_EXTENSION_EXPORTS = [
    "ORCHESTRATION_EXTENSION_URI",
    "STEER_METHOD",
    "Budget",
    "ExecutionRef",
    "Usage",
    "build_delegation_meta",
    "error_meta",
    "extension_agent_extension",
    "is_extension_active",
    "paused_meta",
    "read_delegation_meta",
    "read_usage_meta",
    "steer_notification",
    "usage_meta",
]

# The legacy A2A team app — the one part of the `a2a` subpackage still
# blocked on the message-shape drift `a2a/__init__.py` documents. Guarded on
# its own so the two unrelated failure modes are never conflated.
try:
    from .a2a import A2AChannel, A2ATeamApp, CompositeChannel, TeamMemberWorker, TeamTaskStorage
    from .a2a import create_a2a_team_app

    _A2A_APP_EXPORTS = [
        "A2AChannel",
        "A2ATeamApp",
        "CompositeChannel",
        "TeamMemberWorker",
        "TeamTaskStorage",
        "create_a2a_team_app",
    ]
except ImportError:
    _A2A_APP_EXPORTS = []

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
    # A2A: team-coordination extension (conditional on fasta2a)
    *_A2A_EXPORTS,
    # A2A: the orchestration extension (unconditional; no legacy dependency)
    *_ORCHESTRATION_EXTENSION_EXPORTS,
    # A2A: the legacy team app (conditional on fasta2a's current message shape)
    *_A2A_APP_EXPORTS,
]
