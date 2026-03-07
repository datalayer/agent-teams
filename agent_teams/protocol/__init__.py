# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Coordination protocol for Agent Teams.

This package implements the Datalayer Team Coordination Protocol (DTCP),
an extension of the A2A (Agent-to-Agent) protocol designed for multi-agent
team orchestration. It adds team-specific message types on top of A2A's
task-based communication model.
"""

from .channel import ChannelTransport, InMemoryChannel, TeamChannel
from .messages import (
    CoordinationMessage,
    MemberHeartbeat,
    MemberStatusUpdate,
    MessageType,
    TaskAssignment,
    TaskDelegation,
    TaskResultMessage,
    TeamBroadcast,
    TeamCommand,
    TeamCommandType,
)

__all__ = [
    "ChannelTransport",
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
    "TeamCommandType",
]
