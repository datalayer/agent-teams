# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Communication channels for the coordination protocol.

Channels abstract the transport layer for protocol messages, allowing
in-process (asyncio queues), HTTP (A2A REST), or WebSocket transport.

Inspired by:
- pydantic-deepagents' ``TeamMessageBus`` (per-agent asyncio queues)
- gastown's mailbox system (filesystem-based)
- agent-orchestrator's ``runtime.sendMessage()`` pattern
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any, AsyncIterator, Callable, Optional

from .messages import CoordinationMessage, MessageType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Channel transport enum
# ---------------------------------------------------------------------------


class ChannelTransport(str):
    """Transport type for the channel."""

    IN_MEMORY = "in_memory"
    HTTP = "http"
    WEBSOCKET = "websocket"


# ---------------------------------------------------------------------------
# Abstract channel
# ---------------------------------------------------------------------------


class TeamChannel(ABC):
    """Abstract communication channel for team coordination.

    Each member registers on the channel. Messages are routed by
    ``recipient`` or broadcast when ``recipient == "*"``.
    """

    @abstractmethod
    async def register(self, member_id: str) -> None:
        """Register a member on the channel."""

    @abstractmethod
    async def unregister(self, member_id: str) -> None:
        """Unregister a member from the channel."""

    @abstractmethod
    async def send(self, message: CoordinationMessage) -> None:
        """Send a message to the specified recipient(s)."""

    @abstractmethod
    async def receive(
        self,
        member_id: str,
        timeout: float = 0.0,
    ) -> list[CoordinationMessage]:
        """Receive pending messages for a member.

        If ``timeout > 0``, wait up to that many seconds for messages.
        If ``timeout == 0``, return immediately with queued messages.
        """

    @abstractmethod
    async def subscribe(
        self,
        member_id: str,
    ) -> AsyncIterator[CoordinationMessage]:
        """Subscribe to a continuous stream of messages."""

    @abstractmethod
    async def broadcast(self, message: CoordinationMessage) -> None:
        """Broadcast a message to all registered members."""

    @abstractmethod
    def get_registered_members(self) -> list[str]:
        """Return list of registered member IDs."""


# ---------------------------------------------------------------------------
# In-memory channel (single-process)
# ---------------------------------------------------------------------------


class InMemoryChannel(TeamChannel):
    """In-process channel using asyncio queues.

    Each registered member gets a dedicated ``asyncio.Queue``. Messages
    are routed to specific recipients or broadcast to all members.

    Supports message handlers for type-specific processing:

    .. code-block:: python

        channel = InMemoryChannel()
        channel.add_handler(MessageType.TASK_RESULT, my_handler)
    """

    def __init__(self, max_queue_size: int = 1000) -> None:
        self._queues: dict[str, asyncio.Queue[CoordinationMessage]] = {}
        self._handlers: dict[MessageType, list[Callable]] = defaultdict(list)
        self._max_queue_size = max_queue_size
        self._lock = asyncio.Lock()

    async def register(self, member_id: str) -> None:
        async with self._lock:
            if member_id not in self._queues:
                self._queues[member_id] = asyncio.Queue(maxsize=self._max_queue_size)
                logger.debug("Registered member %s on channel", member_id)

    async def unregister(self, member_id: str) -> None:
        async with self._lock:
            self._queues.pop(member_id, None)
            logger.debug("Unregistered member %s from channel", member_id)

    async def send(self, message: CoordinationMessage) -> None:
        """Route message to the specific recipient."""
        # Fire handlers
        for handler in self._handlers.get(message.type, []):
            try:
                result = handler(message)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("Handler error for %s", message.type)

        # Broadcast case
        if message.recipient == "*":
            await self.broadcast(message)
            return

        queue = self._queues.get(message.recipient)
        if queue is None:
            logger.warning(
                "Message to unregistered member %s (type=%s)",
                message.recipient,
                message.type,
            )
            return

        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            logger.warning("Queue full for member %s, dropping message", message.recipient)

    async def receive(
        self,
        member_id: str,
        timeout: float = 0.0,
    ) -> list[CoordinationMessage]:
        queue = self._queues.get(member_id)
        if queue is None:
            return []

        messages: list[CoordinationMessage] = []

        if timeout > 0 and queue.empty():
            # Wait for at least one message
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=timeout)
                messages.append(msg)
            except asyncio.TimeoutError:
                return messages

        # Drain remaining messages
        while not queue.empty():
            try:
                messages.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        return messages

    async def subscribe(
        self,
        member_id: str,
    ) -> AsyncIterator[CoordinationMessage]:
        queue = self._queues.get(member_id)
        if queue is None:
            return

        while True:
            try:
                message = await queue.get()
                yield message
            except asyncio.CancelledError:
                break

    async def broadcast(self, message: CoordinationMessage) -> None:
        for member_id, queue in self._queues.items():
            if member_id == message.sender:
                continue
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                logger.warning(
                    "Queue full for member %s during broadcast, dropping",
                    member_id,
                )

    def get_registered_members(self) -> list[str]:
        return list(self._queues.keys())

    def add_handler(
        self,
        message_type: MessageType,
        handler: Callable[[CoordinationMessage], Any],
    ) -> None:
        """Register a handler for a specific message type."""
        self._handlers[message_type].append(handler)

    def remove_handler(
        self,
        message_type: MessageType,
        handler: Callable[[CoordinationMessage], Any],
    ) -> None:
        """Remove a previously registered handler."""
        handlers = self._handlers.get(message_type, [])
        if handler in handlers:
            handlers.remove(handler)
