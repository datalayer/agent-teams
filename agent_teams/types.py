# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Core types and Pydantic models for Agent Teams.

These models define the data structures for team configuration,
member agents, tasks, execution state, and metrics. They align
with the UI's TeamData/AgentMember TypeScript interfaces.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TeamStatus(str, Enum):
    """Lifecycle status of a team."""

    DRAFT = "draft"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


class MemberStatus(str, Enum):
    """Runtime status of an individual agent member."""

    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    WAITING = "waiting"
    ERROR = "error"
    COMPLETED = "completed"
    TERMINATED = "terminated"


class ExecutionMode(str, Enum):
    """How agents within a team are orchestrated."""

    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    SUPERVISOR = "supervisor"
    GRAPH = "graph"


class ApprovalPolicy(str, Enum):
    """Whether tasks assigned to a member require human approval."""

    AUTO = "auto"
    MANUAL = "manual"


class TaskStatus(str, Enum):
    """Lifecycle of a task within a team."""

    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class TaskPriority(int, Enum):
    """Task priority levels (lower number = higher priority)."""

    CRITICAL = 0
    HIGH = 1
    MEDIUM = 2
    LOW = 3
    BACKLOG = 4


class EventType(str, Enum):
    """Types of events emitted during team execution."""

    # Team lifecycle
    TEAM_CREATED = "team.created"
    TEAM_STARTED = "team.started"
    TEAM_PAUSED = "team.paused"
    TEAM_RESUMED = "team.resumed"
    TEAM_STOPPED = "team.stopped"
    TEAM_COMPLETED = "team.completed"
    TEAM_FAILED = "team.failed"

    # Member lifecycle
    MEMBER_JOINED = "member.joined"
    MEMBER_LEFT = "member.left"
    MEMBER_STARTED = "member.started"
    MEMBER_IDLE = "member.idle"
    MEMBER_ERROR = "member.error"
    MEMBER_HEARTBEAT = "member.heartbeat"

    # Task lifecycle
    TASK_CREATED = "task.created"
    TASK_ASSIGNED = "task.assigned"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_DELEGATED = "task.delegated"
    TASK_CANCELLED = "task.cancelled"

    # Communication
    MESSAGE_SENT = "message.sent"
    MESSAGE_BROADCAST = "message.broadcast"
    ARTIFACT_PRODUCED = "artifact.produced"


class OrchestrationProtocol(str, Enum):
    """The coordination protocol used between agents."""

    DATALAYER = "datalayer"
    A2A = "a2a"
    A2A_EXTENDED = "a2a-extended"


# ---------------------------------------------------------------------------
# Configuration models
# ---------------------------------------------------------------------------


class SupervisorConfig(BaseModel):
    """Configuration for the supervisor agent."""

    name: str = "Supervisor Agent"
    model: str = "anthropic-claude-sonnet-4"
    provider: str = "Anthropic"
    system_prompt: str = ""
    routing_instructions: str = ""
    agent_spec_id: Optional[str] = None


class ValidationConfig(BaseModel):
    """Execution validation and retry settings."""

    timeout_seconds: int = Field(default=300, ge=1)
    retry_on_failure: bool = True
    max_retries: int = Field(default=3, ge=0)
    heartbeat_interval_seconds: int = Field(default=30, ge=5)


class NotificationConfig(BaseModel):
    """Notification channel configuration."""

    email: Optional[str] = None
    slack: Optional[str] = None
    teams: Optional[str] = None
    webhook_url: Optional[str] = None


class OutputConfig(BaseModel):
    """Output and artifact configuration."""

    formats: list[str] = Field(default_factory=lambda: ["JSON"])
    template: str = ""
    storage: str = ""


# ---------------------------------------------------------------------------
# Member / Agent models
# ---------------------------------------------------------------------------


class AgentMemberConfig(BaseModel):
    """Definition of an agent member within a team.

    Each member wraps an agent-runtime (via agent_spec_id or
    a direct endpoint URL) and has a role, goal, model, and tools.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    role: str = "Member"
    goal: str = ""
    model: str = "anthropic-claude-sonnet-4"
    agent_spec_id: Optional[str] = None
    agent_endpoint: Optional[str] = None
    mcp_server: str = ""
    tools: list[str] = Field(default_factory=list)
    trigger: str = "on_assign"
    approval: ApprovalPolicy = ApprovalPolicy.AUTO
    system_prompt: str = ""
    max_concurrent_tasks: int = Field(default=1, ge=1)


class AgentMemberState(BaseModel):
    """Runtime state of an agent member."""

    id: str
    name: str
    role: str
    status: MemberStatus = MemberStatus.IDLE
    current_task_id: Optional[str] = None
    tasks_completed: int = 0
    tasks_failed: int = 0
    tokens_used: int = 0
    last_heartbeat: Optional[datetime] = None
    error_message: Optional[str] = None


# ---------------------------------------------------------------------------
# Task models
# ---------------------------------------------------------------------------


class TaskDefinition(BaseModel):
    """A unit of work to be performed by a team member."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    title: str
    description: str = ""
    priority: TaskPriority = TaskPriority.MEDIUM
    assigned_to: Optional[str] = None
    created_by: str = "supervisor"
    depends_on: list[str] = Field(default_factory=list)
    input_data: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[TaskResult] = None
    retry_count: int = 0


class TaskResult(BaseModel):
    """Result produced by a completed task."""

    task_id: str
    member_id: str
    status: TaskStatus
    output: Any = None
    artifacts: list[Artifact] = Field(default_factory=list)
    error: Optional[str] = None
    tokens_used: int = 0
    duration_seconds: float = 0.0


class Artifact(BaseModel):
    """An output artifact produced by an agent."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    content_type: str = "text/plain"
    content: Any = None
    uri: Optional[str] = None
    size_bytes: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)


# Fix forward reference
TaskDefinition.model_rebuild()


# ---------------------------------------------------------------------------
# Team models
# ---------------------------------------------------------------------------


class TeamConfig(BaseModel):
    """Full configuration for creating or updating an agent team.

    Aligns with the UI's AgentTeamNew form fields.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    name: str
    description: str = ""
    owner: str = ""

    # Orchestration
    orchestration_protocol: OrchestrationProtocol = OrchestrationProtocol.DATALAYER
    execution_mode: ExecutionMode = ExecutionMode.SUPERVISOR
    supervisor: SupervisorConfig = Field(default_factory=SupervisorConfig)
    routing_instructions: str = ""

    # Members
    members: list[AgentMemberConfig] = Field(default_factory=list)
    initiator_member_id: Optional[str] = None

    # Validation & retries
    validation: ValidationConfig = Field(default_factory=ValidationConfig)

    # Notifications
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)

    # Outputs
    outputs: OutputConfig = Field(default_factory=OutputConfig)

    # Metadata
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TeamState(BaseModel):
    """Runtime state of an active team.

    Aligns with the UI's TeamData interface for the monitoring view.
    """

    id: str
    config: TeamConfig
    status: TeamStatus = TeamStatus.DRAFT
    members: list[AgentMemberState] = Field(default_factory=list)
    tasks: list[TaskDefinition] = Field(default_factory=list)

    # Metrics
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_tokens_used: int = 0
    estimated_cost_usd: float = 0.0
    notifications_sent: int = 0

    # Activity log
    events: list[TeamEvent] = Field(default_factory=list)


class TeamEvent(BaseModel):
    """An event in the team's activity timeline."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    type: EventType
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    source: str = ""
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


# Fix forward reference
TeamState.model_rebuild()


# ---------------------------------------------------------------------------
# API request/response models
# ---------------------------------------------------------------------------


class CreateTeamRequest(BaseModel):
    """Request body for creating a new team."""

    config: TeamConfig


class UpdateTeamRequest(BaseModel):
    """Request body for updating a team."""

    name: Optional[str] = None
    description: Optional[str] = None
    execution_mode: Optional[ExecutionMode] = None
    routing_instructions: Optional[str] = None
    validation: Optional[ValidationConfig] = None
    notifications: Optional[NotificationConfig] = None
    outputs: Optional[OutputConfig] = None


class AssignTaskRequest(BaseModel):
    """Request to assign a task to the team."""

    title: str
    description: str = ""
    priority: TaskPriority = TaskPriority.MEDIUM
    assigned_to: Optional[str] = None
    input_data: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)


class TeamSummary(BaseModel):
    """Summary view of a team for listing."""

    id: str
    name: str
    description: str = ""
    owner: str = ""
    status: TeamStatus
    member_count: int = 0
    active_tasks: int = 0
    execution_mode: ExecutionMode = ExecutionMode.SUPERVISOR
    orchestration_protocol: OrchestrationProtocol = OrchestrationProtocol.DATALAYER
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class TeamMetrics(BaseModel):
    """Metrics snapshot for a running team (for the UI sidebar)."""

    run_status: str = "Unknown"
    duration_seconds: float = 0.0
    estimated_cost_usd: float = 0.0
    total_tokens_used: int = 0
    agents_active: int = 0
    agents_total: int = 0
    tasks_completed: int = 0
    tasks_pending: int = 0
    tasks_failed: int = 0
    notifications_sent: int = 0
