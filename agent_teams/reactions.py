# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Reaction engine for automatic event handling.

When a team event occurs (task failure, member stuck, etc.), the reaction
engine determines whether to auto-handle it or escalate to a human.

Inspired by:
- Agent-orchestrator's reaction engine (YAML-configured actions with retry + escalation)
- Gastown's two-tier event handling (auto-handle routine, escalate for judgment)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reaction types
# ---------------------------------------------------------------------------


class ReactionAction(str, Enum):
    """What to do when a reaction fires."""

    SEND_TO_AGENT = "send-to-agent"  # Send fix instructions to the agent
    REASSIGN = "reassign"  # Reassign task to another member
    RESTART_MEMBER = "restart-member"  # Restart the failed member
    NOTIFY = "notify"  # Notify human
    AUTO_MERGE = "auto-merge"  # Auto-merge (if approved + green)
    CUSTOM = "custom"  # Run a custom callback


class ReactionPriority(str, Enum):
    """Priority for human notifications."""

    INFO = "info"
    WARNING = "warning"
    ACTION = "action"  # Human action needed
    URGENT = "urgent"  # Immediate attention


class ReactionTrigger(str, Enum):
    """Events that can trigger reactions."""

    TASK_FAILED = "task-failed"
    TASK_STUCK = "task-stuck"
    MEMBER_UNRESPONSIVE = "member-unresponsive"
    MEMBER_STUCK = "member-stuck"
    MEMBER_DEAD = "member-dead"
    ALL_COMPLETE = "all-complete"
    MASS_DEATH = "mass-death"


@dataclass
class ReactionConfig:
    """Configuration for a single reaction rule."""

    trigger: ReactionTrigger
    action: ReactionAction
    auto: bool = True  # If False, always notify instead
    priority: ReactionPriority = ReactionPriority.WARNING
    max_retries: int = 2
    escalate_after_retries: int = 2  # Escalate to human after N retries
    escalate_after_seconds: float = 1800.0  # Escalate after 30 min
    message_template: str = ""  # Template for send-to-agent action
    custom_handler: Optional[Callable[..., Coroutine[Any, Any, None]]] = None


@dataclass
class ReactionState:
    """Tracks the state of a reaction for a specific event instance."""

    trigger: ReactionTrigger
    target_id: str  # task_id, member_id, etc.
    first_triggered: float = field(default_factory=time.time)
    attempt_count: int = 0
    last_attempt: Optional[float] = None
    escalated: bool = False
    resolved: bool = False


class ReactionEngine:
    """Processes events and executes configured reactions.

    The engine maintains reaction rules and tracks per-event state to
    implement retry and escalation logic.

    Usage::

        engine = ReactionEngine()
        engine.add_rule(ReactionConfig(
            trigger=ReactionTrigger.TASK_FAILED,
            action=ReactionAction.REASSIGN,
            max_retries=2,
            escalate_after_retries=2,
        ))

        # Set handlers
        engine.set_send_handler(my_send_fn)
        engine.set_notify_handler(my_notify_fn)
        engine.set_reassign_handler(my_reassign_fn)

        # Fire a reaction
        await engine.fire(ReactionTrigger.TASK_FAILED, target_id="task-1", context={...})
    """

    def __init__(self) -> None:
        self._rules: dict[ReactionTrigger, ReactionConfig] = {}
        self._states: dict[str, ReactionState] = {}

        # Configurable action handlers
        self._send_handler: Optional[Callable[..., Coroutine[Any, Any, None]]] = None
        self._notify_handler: Optional[Callable[..., Coroutine[Any, Any, None]]] = None
        self._reassign_handler: Optional[Callable[..., Coroutine[Any, Any, None]]] = None
        self._restart_handler: Optional[Callable[..., Coroutine[Any, Any, None]]] = None

    def add_rule(self, config: ReactionConfig) -> None:
        """Register a reaction rule for a trigger."""
        self._rules[config.trigger] = config

    def set_send_handler(
        self, handler: Callable[..., Coroutine[Any, Any, None]]
    ) -> None:
        """Set handler for SEND_TO_AGENT action."""
        self._send_handler = handler

    def set_notify_handler(
        self, handler: Callable[..., Coroutine[Any, Any, None]]
    ) -> None:
        """Set handler for NOTIFY action."""
        self._notify_handler = handler

    def set_reassign_handler(
        self, handler: Callable[..., Coroutine[Any, Any, None]]
    ) -> None:
        """Set handler for REASSIGN action."""
        self._reassign_handler = handler

    def set_restart_handler(
        self, handler: Callable[..., Coroutine[Any, Any, None]]
    ) -> None:
        """Set handler for RESTART_MEMBER action."""
        self._restart_handler = handler

    async def fire(
        self,
        trigger: ReactionTrigger,
        target_id: str,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Fire a reaction for the given trigger.

        Returns True if the reaction was executed, False if escalated or no rule found.
        """
        rule = self._rules.get(trigger)
        if rule is None:
            logger.debug("No reaction rule for %s", trigger.value)
            return False

        # Get or create reaction state
        state_key = f"{trigger.value}:{target_id}"
        state = self._states.get(state_key)
        if state is None:
            state = ReactionState(trigger=trigger, target_id=target_id)
            self._states[state_key] = state

        if state.resolved:
            return False

        now = time.time()

        # Check if we should escalate
        should_escalate = (
            state.attempt_count >= rule.escalate_after_retries
            or (now - state.first_triggered) > rule.escalate_after_seconds
        )

        if should_escalate and not state.escalated:
            state.escalated = True
            logger.warning(
                "Escalating %s for %s (attempts=%d, elapsed=%.0fs)",
                trigger.value,
                target_id,
                state.attempt_count,
                now - state.first_triggered,
            )
            if self._notify_handler:
                await self._notify_handler(
                    trigger=trigger,
                    target_id=target_id,
                    priority=ReactionPriority.URGENT,
                    message=f"Escalated: {trigger.value} for {target_id} after {state.attempt_count} attempts",
                    context=context or {},
                )
            return False

        if not rule.auto:
            # Non-auto rules always notify
            if self._notify_handler:
                await self._notify_handler(
                    trigger=trigger,
                    target_id=target_id,
                    priority=rule.priority,
                    message=rule.message_template or f"{trigger.value} for {target_id}",
                    context=context or {},
                )
            return True

        # Execute the reaction action
        state.attempt_count += 1
        state.last_attempt = now

        try:
            await self._execute_action(rule, target_id, context or {})
            logger.info(
                "Reaction %s executed for %s (attempt %d)",
                rule.action.value,
                target_id,
                state.attempt_count,
            )
            return True
        except Exception:
            logger.exception(
                "Reaction %s failed for %s (attempt %d)",
                rule.action.value,
                target_id,
                state.attempt_count,
            )
            return False

    def resolve(self, trigger: ReactionTrigger, target_id: str) -> None:
        """Mark a reaction as resolved (e.g., task succeeded after retry)."""
        state_key = f"{trigger.value}:{target_id}"
        state = self._states.get(state_key)
        if state:
            state.resolved = True

    def get_state(self, trigger: ReactionTrigger, target_id: str) -> Optional[ReactionState]:
        """Get the state of a specific reaction."""
        return self._states.get(f"{trigger.value}:{target_id}")

    def reset(self) -> None:
        """Clear all reaction states."""
        self._states.clear()

    async def _execute_action(
        self,
        rule: ReactionConfig,
        target_id: str,
        context: dict[str, Any],
    ) -> None:
        """Execute a reaction action."""
        if rule.action == ReactionAction.SEND_TO_AGENT:
            if self._send_handler:
                await self._send_handler(
                    target_id=target_id,
                    message=rule.message_template,
                    context=context,
                )
        elif rule.action == ReactionAction.REASSIGN:
            if self._reassign_handler:
                await self._reassign_handler(target_id=target_id, context=context)
        elif rule.action == ReactionAction.RESTART_MEMBER:
            if self._restart_handler:
                await self._restart_handler(target_id=target_id, context=context)
        elif rule.action == ReactionAction.NOTIFY:
            if self._notify_handler:
                await self._notify_handler(
                    trigger=rule.trigger,
                    target_id=target_id,
                    priority=rule.priority,
                    message=rule.message_template or f"{rule.trigger.value} for {target_id}",
                    context=context,
                )
        elif rule.action == ReactionAction.CUSTOM:
            if rule.custom_handler:
                await rule.custom_handler(target_id=target_id, context=context)
