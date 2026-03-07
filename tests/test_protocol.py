# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Tests for the coordination protocol (messages and channels)."""

from __future__ import annotations

import asyncio

import pytest

from agent_teams.protocol.channel import InMemoryChannel
from agent_teams.protocol.messages import (
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
from agent_teams.types import MemberStatus, TaskStatus


class TestMessages:
    """Test protocol message creation and serialization."""

    def test_coordination_message_defaults(self):
        msg = CoordinationMessage(
            type=MessageType.TEAM_BROADCAST,
            team_id="team-1",
            sender="manager",
        )
        assert msg.id  # UUID auto-generated
        assert msg.timestamp is not None

    def test_team_command(self):
        cmd = TeamCommand(
            team_id="team-1",
            sender="manager",
            command=TeamCommandType.START,
        )
        assert cmd.type == MessageType.TEAM_START
        assert cmd.command == TeamCommandType.START

    def test_task_assignment(self):
        ta = TaskAssignment(
            team_id="team-1",
            sender="supervisor",
            recipient="worker-1",
            task_id="task-1",
            title="Code review",
            description="Review the PR",
            timeout_seconds=300,
        )
        assert ta.type == MessageType.TASK_ASSIGN
        assert ta.timeout_seconds == 300

    def test_task_delegation(self):
        td = TaskDelegation(
            team_id="team-1",
            sender="worker-1",
            recipient="worker-2",
            task_id="task-2",
            parent_task_id="task-1",
            title="Sub-task",
            description="Do the sub-task",
        )
        assert td.type == MessageType.TASK_DELEGATE

    def test_task_result_message(self):
        tr = TaskResultMessage(
            team_id="team-1",
            sender="worker-1",
            recipient="supervisor",
            task_id="task-1",
            output="Done!",
            status=TaskStatus.COMPLETED,
        )
        assert tr.type == MessageType.TASK_RESULT
        assert tr.status == TaskStatus.COMPLETED

    def test_member_heartbeat(self):
        hb = MemberHeartbeat(
            team_id="team-1",
            sender="worker-1",
            uptime_seconds=120.5,
        )
        assert hb.type == MessageType.MEMBER_HEARTBEAT
        assert hb.uptime_seconds == 120.5

    def test_member_status_update(self):
        su = MemberStatusUpdate(
            team_id="team-1",
            sender="worker-1",
            status=MemberStatus.RUNNING,
        )
        assert su.type == MessageType.MEMBER_STATUS
        assert su.status == MemberStatus.RUNNING

    def test_team_broadcast(self):
        bc = TeamBroadcast(
            team_id="team-1",
            sender="supervisor",
            content="All hands meeting!",
        )
        assert bc.type == MessageType.TEAM_BROADCAST

    def test_reply_creates_response(self):
        original = CoordinationMessage(
            type=MessageType.TASK_ASSIGN,
            team_id="team-1",
            sender="supervisor",
            recipient="worker-1",
        )
        reply = original.reply(
            type=MessageType.TASK_ACCEPT,
            payload={"accepted": True},
        )
        assert reply.sender == "worker-1"
        assert reply.recipient == "supervisor"
        assert reply.correlation_id == original.id


class TestInMemoryChannel:
    """Test the in-memory channel implementation."""

    @pytest.fixture
    def channel(self) -> InMemoryChannel:
        return InMemoryChannel()

    async def test_register_and_unregister(self, channel: InMemoryChannel):
        await channel.register("member-1")
        assert "member-1" in channel.get_registered_members()
        await channel.unregister("member-1")
        assert "member-1" not in channel.get_registered_members()

    async def test_send_and_receive(self, channel: InMemoryChannel):
        await channel.register("sender")
        await channel.register("receiver")

        msg = CoordinationMessage(
            type=MessageType.TASK_ASSIGN,
            team_id="team-1",
            sender="sender",
            recipient="receiver",
        )
        await channel.send(msg)
        received = await channel.receive("receiver", timeout=1.0)

        assert len(received) == 1
        assert received[0].id == msg.id

    async def test_receive_timeout(self, channel: InMemoryChannel):
        await channel.register("someone")
        received = await channel.receive("someone", timeout=0.1)
        assert received == []

    async def test_broadcast(self, channel: InMemoryChannel):
        await channel.register("a")
        await channel.register("b")
        await channel.register("c")

        msg = CoordinationMessage(
            type=MessageType.TEAM_BROADCAST,
            team_id="team-1",
            sender="a",
        )
        await channel.broadcast(msg)

        # Both b and c should receive it, but not a (sender)
        msgs_b = await channel.receive("b", timeout=1.0)
        msgs_c = await channel.receive("c", timeout=1.0)
        msgs_a = await channel.receive("a", timeout=0.1)

        assert len(msgs_b) == 1
        assert len(msgs_c) == 1
        assert len(msgs_a) == 0

    async def test_add_handler(self, channel: InMemoryChannel):
        await channel.register("worker")
        received: list[CoordinationMessage] = []

        async def handler(msg: CoordinationMessage):
            received.append(msg)

        channel.add_handler(MessageType.TASK_ASSIGN, handler)

        msg = CoordinationMessage(
            type=MessageType.TASK_ASSIGN,
            team_id="team-1",
            sender="supervisor",
            recipient="worker",
        )
        await channel.send(msg)

        # Give async handler time to execute
        await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[0].id == msg.id

    async def test_send_to_unregistered_member(self, channel: InMemoryChannel):
        await channel.register("sender")
        msg = CoordinationMessage(
            type=MessageType.TASK_ASSIGN,
            team_id="team-1",
            sender="sender",
            recipient="nobody",
        )
        # Should not raise, just log a warning
        await channel.send(msg)
