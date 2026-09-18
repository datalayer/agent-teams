# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Tests for A2A channel adapters (A2AChannel, CompositeChannel)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_teams.a2a.channel import (
    A2AChannel,
    CompositeChannel,
    _coordination_to_a2a_message,
)
from agent_teams.protocol.channel import InMemoryChannel
from agent_teams.protocol.messages import CoordinationMessage, MessageType


def _make_msg(
    sender: str = "agent-1",
    recipient: str = "agent-2",
    team_id: str = "team-1",
    payload: dict | None = None,
) -> CoordinationMessage:
    """Create a test coordination message."""
    return CoordinationMessage(
        type=MessageType.TASK_ASSIGN,
        sender=sender,
        recipient=recipient,
        team_id=team_id,
        payload=payload or {"text": "hello"},
    )


# ---------------------------------------------------------------------------
# _coordination_to_a2a_message conversion
# ---------------------------------------------------------------------------


class TestCoordinationToA2A:
    """Test the conversion from CoordinationMessage to A2A Message."""

    def test_basic_conversion(self) -> None:
        msg = _make_msg(payload={"text": "do this"})
        a2a_msg = _coordination_to_a2a_message(msg)

        assert a2a_msg["role"] == "user"
        # A2A v1: a message carries no `kind` discriminator.
        assert "kind" not in a2a_msg
        assert a2a_msg["message_id"] == msg.id
        assert len(a2a_msg["parts"]) == 1
        assert "[task/assign]" in a2a_msg["parts"][0]["text"]
        assert "do this" in a2a_msg["parts"][0]["text"]

    def test_metadata_includes_team_id(self) -> None:
        msg = _make_msg(team_id="team-xyz")
        a2a_msg = _coordination_to_a2a_message(msg)
        assert a2a_msg.get("metadata", {}).get("team_id") == "team-xyz"

    def test_metadata_includes_sender(self) -> None:
        msg = _make_msg(sender="supervisor")
        a2a_msg = _coordination_to_a2a_message(msg)
        assert a2a_msg.get("metadata", {}).get("sender") == "supervisor"

    def test_empty_payload(self) -> None:
        msg = _make_msg(payload={})
        a2a_msg = _coordination_to_a2a_message(msg)
        # Empty dict is falsy, so it should just have the type
        assert "[task/assign]" in a2a_msg["parts"][0]["text"]


# ---------------------------------------------------------------------------
# A2AChannel
# ---------------------------------------------------------------------------


class TestA2AChannel:
    """Tests for the A2A remote communication channel."""

    @pytest.mark.asyncio
    async def test_register_with_url(self) -> None:
        ch = A2AChannel()
        await ch.register("agent-1", base_url="http://localhost:8001")

        assert "agent-1" in ch.get_registered_members()
        assert "agent-1" in ch.get_remote_members()

    @pytest.mark.asyncio
    async def test_register_without_url(self) -> None:
        ch = A2AChannel()
        await ch.register("agent-local")

        assert "agent-local" in ch.get_registered_members()
        assert "agent-local" not in ch.get_remote_members()

    @pytest.mark.asyncio
    async def test_unregister(self) -> None:
        ch = A2AChannel()
        await ch.register("agent-1", base_url="http://localhost:8001")
        await ch.unregister("agent-1")

        assert "agent-1" not in ch.get_registered_members()
        assert "agent-1" not in ch.get_remote_members()

    @pytest.mark.asyncio
    async def test_send_to_local_member(self) -> None:
        ch = A2AChannel()
        await ch.register("agent-local")  # no URL => local

        msg = _make_msg(recipient="agent-local")
        await ch.send(msg)

        received = await ch.receive("agent-local")
        assert len(received) == 1
        assert received[0].payload == {"text": "hello"}

    @pytest.mark.asyncio
    async def test_send_to_unregistered_member(self) -> None:
        ch = A2AChannel()
        msg = _make_msg(recipient="ghost")
        # Should not raise, just warn
        await ch.send(msg)

    @pytest.mark.asyncio
    async def test_receive_empty(self) -> None:
        ch = A2AChannel()
        await ch.register("agent-1")
        received = await ch.receive("agent-1")
        assert received == []

    @pytest.mark.asyncio
    async def test_receive_unregistered(self) -> None:
        ch = A2AChannel()
        received = await ch.receive("nope")
        assert received == []

    @pytest.mark.asyncio
    async def test_receive_with_timeout(self) -> None:
        ch = A2AChannel()
        await ch.register("agent-1")

        # Should return empty after timeout
        received = await ch.receive("agent-1", timeout=0.05)
        assert received == []

    @pytest.mark.asyncio
    async def test_broadcast_to_all(self) -> None:
        ch = A2AChannel()
        await ch.register("agent-1")
        await ch.register("agent-2")
        await ch.register("agent-3")

        msg = _make_msg(sender="agent-1", recipient="*")
        await ch.broadcast(msg)

        # agent-1 is the sender, should not receive
        r1 = await ch.receive("agent-1")
        assert len(r1) == 0

        r2 = await ch.receive("agent-2")
        assert len(r2) == 1

        r3 = await ch.receive("agent-3")
        assert len(r3) == 1

    @pytest.mark.asyncio
    async def test_send_remote_fallback_on_error(self) -> None:
        """When A2A client fails, message should be queued locally."""
        ch = A2AChannel()
        await ch.register("agent-remote", base_url="http://localhost:9999")

        # Mock the client to raise
        mock_client = MagicMock()
        mock_client.send_message = AsyncMock(side_effect=ConnectionError("boom"))
        ch._clients["agent-remote"] = mock_client

        msg = _make_msg(recipient="agent-remote")
        await ch.send(msg)

        # Should have fallen back to local queue
        received = await ch.receive("agent-remote")
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_send_broadcast_wildcard(self) -> None:
        """send() with recipient='*' should call broadcast()."""
        ch = A2AChannel()
        await ch.register("a")
        await ch.register("b")

        msg = _make_msg(sender="a", recipient="*")
        await ch.send(msg)

        received = await ch.receive("b")
        assert len(received) == 1


# ---------------------------------------------------------------------------
# CompositeChannel
# ---------------------------------------------------------------------------


class TestCompositeChannel:
    """Tests for the composite local+remote channel."""

    @pytest.mark.asyncio
    async def test_register_local_member(self) -> None:
        local = InMemoryChannel()
        remote = A2AChannel()
        composite = CompositeChannel(local, remote)

        await composite.register("local-agent")

        assert "local-agent" in composite.get_registered_members()
        assert "local-agent" not in remote.get_remote_members()

    @pytest.mark.asyncio
    async def test_register_remote_member(self) -> None:
        local = InMemoryChannel()
        remote = A2AChannel()
        composite = CompositeChannel(local, remote)

        await composite.register("remote-agent", base_url="http://example.com")

        assert "remote-agent" in composite.get_registered_members()
        assert "remote-agent" in remote.get_remote_members()

    @pytest.mark.asyncio
    async def test_send_routes_to_local(self) -> None:
        local = InMemoryChannel()
        remote = A2AChannel()
        composite = CompositeChannel(local, remote)

        await composite.register("local-1")
        msg = _make_msg(recipient="local-1")
        await composite.send(msg)

        received = await composite.receive("local-1")
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_send_routes_to_remote(self) -> None:
        """Remote member should route through A2AChannel."""
        local = InMemoryChannel()
        remote = A2AChannel()
        composite = CompositeChannel(local, remote)

        await composite.register("remote-1", base_url="http://localhost:9999")

        # Mock the client to succeed
        mock_client = MagicMock()
        mock_client.send_message = AsyncMock(return_value={"result": {"id": "t1"}})
        remote._clients["remote-1"] = mock_client

        msg = _make_msg(recipient="remote-1")
        await composite.send(msg)

        mock_client.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_to_both_channels(self) -> None:
        local = InMemoryChannel()
        remote = A2AChannel()
        composite = CompositeChannel(local, remote)

        await composite.register("local-a")
        await composite.register("remote-b", base_url="http://localhost:9999")

        # Mock remote client
        mock_client = MagicMock()
        mock_client.send_message = AsyncMock(return_value={"result": {"id": "t1"}})
        remote._clients["remote-b"] = mock_client

        msg = _make_msg(sender="supervisor", recipient="*")
        await composite.broadcast(msg)

        local_received = await local.receive("local-a")
        assert len(local_received) == 1

    @pytest.mark.asyncio
    async def test_unregister_from_both(self) -> None:
        local = InMemoryChannel()
        remote = A2AChannel()
        composite = CompositeChannel(local, remote)

        await composite.register("agent-x")
        await composite.register("agent-y", base_url="http://localhost:9999")

        await composite.unregister("agent-x")
        await composite.unregister("agent-y")

        assert "agent-x" not in composite.get_registered_members()
        assert "agent-y" not in composite.get_registered_members()
