# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Protocol message types for team coordination.

The Datalayer Team Coordination Protocol (DTCP) extends A2A with
team-specific message types. Every message has:

- A ``type`` discriminator following A2A's ``method`` convention.
- A ``sender`` / ``recipient`` addressing pair (agent IDs or ``*`` for broadcast).
- An optional ``team_id`` scope.
- A ``correlation_id`` for request/response matching.

Message types are grouped into three categories:

1. **Team commands** — lifecycle operations (create, start, pause, stop).
2. **Task messages** — assignment, delegation, result reporting.
3. **Member messages** — heartbeat, status updates, capability announcements.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..types import MemberStatus, TaskPriority, TaskStatus


# ---------------------------------------------------------------------------
# Message type enum
# ---------------------------------------------------------------------------


class MessageType(str, Enum):
    """All message types in the coordination protocol."""

    # Team lifecycle
    TEAM_CREATE = "team/create"
    TEAM_START = "team/start"
    TEAM_PAUSE = "team/pause"
    TEAM_RESUME = "team/resume"
    TEAM_STOP = "team/stop"

    # Task messages
    TASK_ASSIGN = "task/assign"
    TASK_DELEGATE = "task/delegate"
    TASK_ACCEPT = "task/accept"
    TASK_REJECT = "task/reject"
    TASK_PROGRESS = "task/progress"
    TASK_RESULT = "task/result"
    TASK_CANCEL = "task/cancel"

    # Member messages
    MEMBER_HEARTBEAT = "member/heartbeat"
    MEMBER_STATUS = "member/status"
    MEMBER_CAPABILITIES = "member/capabilities"

    # Broadcast
    TEAM_BROADCAST = "team/broadcast"

    # A2A compatibility
    A2A_TASK_SEND = "a2a/tasks/send"
    A2A_TASK_GET = "a2a/tasks/get"
    A2A_TASK_CANCEL = "a2a/tasks/cancel"


class TeamCommandType(str, Enum):
    """Team lifecycle commands."""

    CREATE = "create"
    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"


# ---------------------------------------------------------------------------
# Base message
# ---------------------------------------------------------------------------


class CoordinationMessage(BaseModel):
    """Base class for all coordination protocol messages.

    Follows A2A's JSON-RPC-inspired structure with ``method`` (type),
    ``id`` (correlation), and ``params`` (payload).
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: MessageType
    team_id: str = ""
    sender: str = ""
    recipient: str = ""  # "*" for broadcast
    correlation_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    payload: dict[str, Any] = Field(default_factory=dict)

    def reply(self, type: MessageType, payload: dict[str, Any] | None = None) -> CoordinationMessage:
        """Create a reply message with swapped sender/recipient."""
        return CoordinationMessage(
            type=type,
            team_id=self.team_id,
            sender=self.recipient,
            recipient=self.sender,
            correlation_id=self.id,
            payload=payload or {},
        )


# ---------------------------------------------------------------------------
# Team commands
# ---------------------------------------------------------------------------


class TeamCommand(CoordinationMessage):
    """A team lifecycle command (create, start, pause, stop)."""

    type: MessageType = MessageType.TEAM_START
    command: TeamCommandType = TeamCommandType.START


# ---------------------------------------------------------------------------
# Task messages
# ---------------------------------------------------------------------------


class TaskAssignment(CoordinationMessage):
    """Supervisor assigns a task to a member."""

    type: MessageType = MessageType.TASK_ASSIGN
    task_id: str = ""
    title: str = ""
    description: str = ""
    priority: TaskPriority = TaskPriority.MEDIUM
    input_data: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = 300


class TaskDelegation(CoordinationMessage):
    """A member delegates a sub-task to another member."""

    type: MessageType = MessageType.TASK_DELEGATE
    parent_task_id: str = ""
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    title: str = ""
    description: str = ""
    input_data: dict[str, Any] = Field(default_factory=dict)


class TaskResultMessage(CoordinationMessage):
    """A member reports a task result back to the supervisor."""

    type: MessageType = MessageType.TASK_RESULT
    task_id: str = ""
    status: TaskStatus = TaskStatus.COMPLETED
    output: Any = None
    error: Optional[str] = None
    tokens_used: int = 0
    duration_seconds: float = 0.0
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Member messages
# ---------------------------------------------------------------------------


class MemberHeartbeat(CoordinationMessage):
    """Periodic heartbeat from a member agent."""

    type: MessageType = MessageType.MEMBER_HEARTBEAT
    status: MemberStatus = MemberStatus.IDLE
    current_task_id: Optional[str] = None
    tokens_used: int = 0
    uptime_seconds: float = 0.0


class MemberStatusUpdate(CoordinationMessage):
    """A member reports a status change."""

    type: MessageType = MessageType.MEMBER_STATUS
    status: MemberStatus = MemberStatus.IDLE
    message: str = ""
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------


class TeamBroadcast(CoordinationMessage):
    """A broadcast message to all team members."""

    type: MessageType = MessageType.TEAM_BROADCAST
    recipient: str = "*"
    content: str = ""
    broadcast_data: dict[str, Any] = Field(default_factory=dict)
