# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""A2A-based communication channel for remote agent members.

When team members are running as remote A2A agents (each with their own
fasta2a endpoint), the ``A2AChannel`` sends coordination messages by
translating them into A2A ``message/send`` or ``message/stream`` calls
via fasta2a's ``A2AClient``.

This allows agent-teams to seamlessly coordinate both local (in-process)
and remote (A2A endpoint) agent members using the same orchestration layer.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, AsyncIterator

from fasta2a.client import A2AClient
from fasta2a.schema import Message as A2AMessage, TextPart

from ..protocol.channel import TeamChannel
from ..protocol.messages import CoordinationMessage

logger = logging.getLogger(__name__)


class A2AChannel(TeamChannel):
    """Communication channel that routes messages to remote A2A agents.

    Each member is registered with a base URL pointing to their A2A endpoint.
    Messages are translated to A2A ``message/send`` calls.

    For local (in-process) members, pair with ``InMemoryChannel``. The
    ``TeamManager`` uses a ``CompositeChannel`` that routes to either
    based on member configuration.

    Parameters
    ----------
    default_timeout:
        Timeout in seconds for A2A client calls.
    """

    def __init__(self, default_timeout: float = 30.0) -> None:
        self._members: dict[str, str] = {}  # member_id -> base_url
        self._clients: dict[str, A2AClient] = {}
        self._inboxes: dict[str, asyncio.Queue[CoordinationMessage]] = {}
        self._default_timeout = default_timeout

    async def register(self, member_id: str, base_url: str | None = None) -> None:
        """Register a remote member with their A2A endpoint URL.

        Parameters
        ----------
        member_id:
            The unique identifier for this member.
        base_url:
            The base URL of the member's A2A endpoint, e.g. ``http://localhost:8001``.
            If None, the member is registered without a remote endpoint
            (messages will be queued locally).
        """
        if base_url:
            self._members[member_id] = base_url
            # `agent`, not `base_url` — fasta2a renamed the constructor's
            # worker-URL argument somewhere within the unpinned `fasta2a`
            # floor this package had; every remote member was unreachable
            # until it was found and fixed (datalayer/orchestrator-protocols
            # research, ORCHESTRATOR.md O3-02).
            self._clients[member_id] = A2AClient(agent=base_url)
        self._inboxes[member_id] = asyncio.Queue(maxsize=1000)
        logger.debug("A2AChannel: registered member %s (url=%s)", member_id, base_url)

    async def unregister(self, member_id: str) -> None:
        """Unregister a member from the channel."""
        self._members.pop(member_id, None)
        client = self._clients.pop(member_id, None)
        if client and hasattr(client, "http_client"):
            await client.http_client.aclose()
        self._inboxes.pop(member_id, None)
        logger.debug("A2AChannel: unregistered member %s", member_id)

    async def send(self, message: CoordinationMessage) -> None:
        """Send a coordination message to the recipient.

        If the recipient is a remote A2A agent, the message is translated
        into an A2A ``message/send`` call. Otherwise, it's queued locally.
        """
        if message.recipient == "*":
            await self.broadcast(message)
            return

        recipient = message.recipient
        client = self._clients.get(recipient)

        if client:
            # Remote A2A agent — send via A2A protocol
            a2a_message = _coordination_to_a2a_message(message)
            try:
                response = await client.send_message(a2a_message)
                # If successful, queue any response for the sender
                if "result" in response:
                    logger.debug(
                        "A2A message sent to %s, task=%s",
                        recipient,
                        response.get("result", {}).get("id"),
                    )
            except Exception:
                logger.exception("Failed to send A2A message to %s", recipient)
                # Queue the message locally as fallback
                inbox = self._inboxes.get(recipient)
                if inbox:
                    try:
                        inbox.put_nowait(message)
                    except asyncio.QueueFull:
                        logger.warning("Inbox full for %s, dropping message", recipient)
        else:
            # Local member — queue directly
            inbox = self._inboxes.get(recipient)
            if inbox is None:
                logger.warning("Message to unregistered member %s", recipient)
                return
            try:
                inbox.put_nowait(message)
            except asyncio.QueueFull:
                logger.warning("Inbox full for %s, dropping message", recipient)

    async def receive(
        self,
        member_id: str,
        timeout: float = 0.0,
    ) -> list[CoordinationMessage]:
        """Receive pending messages for a member."""
        inbox = self._inboxes.get(member_id)
        if inbox is None:
            return []

        messages: list[CoordinationMessage] = []

        if timeout > 0 and inbox.empty():
            try:
                msg = await asyncio.wait_for(inbox.get(), timeout=timeout)
                messages.append(msg)
            except asyncio.TimeoutError:
                return messages

        while not inbox.empty():
            try:
                messages.append(inbox.get_nowait())
            except asyncio.QueueEmpty:
                break

        return messages

    async def subscribe(self, member_id: str) -> AsyncIterator[CoordinationMessage]:
        """Subscribe to a continuous stream of messages for a member."""
        inbox = self._inboxes.get(member_id)
        if inbox is None:
            return

        while True:
            try:
                message = await inbox.get()
                yield message
            except asyncio.CancelledError:
                break

    async def broadcast(self, message: CoordinationMessage) -> None:
        """Broadcast a message to all registered members."""
        tasks = []
        for member_id in list(self._inboxes.keys()):
            if member_id == message.sender:
                continue
            msg_copy = message.model_copy(update={"recipient": member_id})
            tasks.append(self.send(msg_copy))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def get_registered_members(self) -> list[str]:
        """Return list of currently registered member IDs."""
        return list(self._inboxes.keys())

    def get_remote_members(self) -> list[str]:
        """Return list of members that have remote A2A endpoints."""
        return list(self._clients.keys())


class CompositeChannel(TeamChannel):
    """A channel that routes to local (InMemoryChannel) or remote (A2AChannel).

    Members without an ``agent_endpoint`` use the local channel; members
    with an ``agent_endpoint`` use the A2A channel.
    """

    def __init__(self, local: TeamChannel, remote: A2AChannel) -> None:
        self._local = local
        self._remote = remote

    async def register(self, member_id: str, **kwargs: Any) -> None:
        """Register a member on the appropriate channel."""
        base_url = kwargs.get("base_url")
        if base_url:
            await self._remote.register(member_id, base_url=base_url)
        else:
            await self._local.register(member_id)

    async def unregister(self, member_id: str) -> None:
        """Unregister from both channels."""
        await self._local.unregister(member_id)
        await self._remote.unregister(member_id)

    async def send(self, message: CoordinationMessage) -> None:
        """Route to the correct channel based on the recipient."""
        if message.recipient == "*":
            await self.broadcast(message)
            return

        if message.recipient in self._remote.get_remote_members():
            await self._remote.send(message)
        else:
            await self._local.send(message)

    async def receive(
        self,
        member_id: str,
        timeout: float = 0.0,
    ) -> list[CoordinationMessage]:
        """Receive from the appropriate channel."""
        if member_id in self._remote.get_remote_members():
            return await self._remote.receive(member_id, timeout=timeout)
        return await self._local.receive(member_id, timeout=timeout)

    async def subscribe(self, member_id: str) -> AsyncIterator[CoordinationMessage]:
        """Subscribe to the appropriate channel."""
        if member_id in self._remote.get_remote_members():
            async for msg in self._remote.subscribe(member_id):
                yield msg
        else:
            async for msg in self._local.subscribe(member_id):
                yield msg

    async def broadcast(self, message: CoordinationMessage) -> None:
        """Broadcast to all members across both channels."""
        await asyncio.gather(
            self._local.broadcast(message),
            self._remote.broadcast(message),
            return_exceptions=True,
        )

    def get_registered_members(self) -> list[str]:
        """Return all registered members from both channels."""
        local_members = set(self._local.get_registered_members())
        remote_members = set(self._remote.get_registered_members())
        return list(local_members | remote_members)


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def _coordination_to_a2a_message(msg: CoordinationMessage) -> A2AMessage:
    """Convert a coordination message to an A2A message for sending."""
    text = f"[{msg.type.value}] {msg.payload}" if msg.payload else f"[{msg.type.value}]"
    return A2AMessage(
        role="user",
        kind="message",
        message_id=msg.id,
        parts=[TextPart(kind="text", text=text)],
        metadata={
            "team_id": msg.team_id,
            "sender": msg.sender,
            "message_type": msg.type.value,
            "correlation_id": msg.correlation_id or "",
        },
    )
