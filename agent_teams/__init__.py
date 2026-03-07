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
# Manager
# ---------------------------------------------------------------------------
from .manager import TeamManager

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
from .app import create_app

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
    # Manager
    "TeamManager",
    # App
    "create_app",
]
