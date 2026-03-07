# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Lifecycle hooks for agent team events.

Hooks allow external code to intercept and react to team lifecycle
events — similar to Gastown's Claude Code hooks (pre/post tool use,
session start/stop) and agent-orchestrator's workspace hooks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)


class HookEvent(str, Enum):
    """Events that hooks can intercept."""

    # Team lifecycle
    PRE_TEAM_START = "pre-team-start"
    POST_TEAM_START = "post-team-start"
    PRE_TEAM_STOP = "pre-team-stop"
    POST_TEAM_STOP = "post-team-stop"

    # Member lifecycle
    PRE_MEMBER_START = "pre-member-start"
    POST_MEMBER_START = "post-member-start"
    PRE_MEMBER_STOP = "pre-member-stop"
    POST_MEMBER_STOP = "post-member-stop"

    # Task lifecycle
    PRE_TASK_ASSIGN = "pre-task-assign"
    POST_TASK_ASSIGN = "post-task-assign"
    PRE_TASK_EXECUTE = "pre-task-execute"
    POST_TASK_EXECUTE = "post-task-execute"

    # Results
    ON_TASK_COMPLETE = "on-task-complete"
    ON_TASK_FAILED = "on-task-failed"
    ON_MEMBER_ERROR = "on-member-error"


@dataclass
class HookResult:
    """Result from a hook execution."""

    allow: bool = True  # If False, the hooked action is prevented
    reason: str = ""
    modified_args: Optional[dict[str, Any]] = None


# Type alias for hook callables
HookFn = Callable[..., Coroutine[Any, Any, HookResult | None]]


@dataclass
class Hook:
    """A registered hook."""

    event: HookEvent
    name: str
    handler: HookFn
    priority: int = 0  # Lower = runs first
    enabled: bool = True


class HookRegistry:
    """Registry and execution engine for lifecycle hooks.

    Hooks are executed in priority order. If any hook returns
    ``HookResult(allow=False)``, the chain is short-circuited.

    Usage::

        registry = HookRegistry()

        @registry.on(HookEvent.PRE_TASK_ASSIGN)
        async def check_approval(event, task, member, **kwargs):
            if member.config.approval == ApprovalPolicy.MANUAL:
                return HookResult(allow=False, reason="Manual approval required")
            return HookResult(allow=True)

        # Execute hooks
        result = await registry.run(HookEvent.PRE_TASK_ASSIGN, task=task, member=member)
        if not result.allow:
            raise PermissionError(result.reason)
    """

    def __init__(self) -> None:
        self._hooks: dict[HookEvent, list[Hook]] = {}

    def register(
        self,
        event: HookEvent,
        handler: HookFn,
        name: str = "",
        priority: int = 0,
    ) -> None:
        """Register a hook for an event."""
        hook = Hook(event=event, name=name or handler.__name__, handler=handler, priority=priority)
        self._hooks.setdefault(event, []).append(hook)
        self._hooks[event].sort(key=lambda h: h.priority)

    def on(self, event: HookEvent, priority: int = 0) -> Callable[[HookFn], HookFn]:
        """Decorator to register a hook."""
        def decorator(fn: HookFn) -> HookFn:
            self.register(event, fn, name=fn.__name__, priority=priority)
            return fn
        return decorator

    def unregister(self, event: HookEvent, name: str) -> bool:
        """Remove a hook by name. Returns True if found."""
        hooks = self._hooks.get(event, [])
        for i, hook in enumerate(hooks):
            if hook.name == name:
                hooks.pop(i)
                return True
        return False

    def get_hooks(self, event: HookEvent) -> list[Hook]:
        """Get all hooks for an event."""
        return [h for h in self._hooks.get(event, []) if h.enabled]

    async def run(self, event: HookEvent, **kwargs: Any) -> HookResult:
        """Execute all hooks for an event in priority order.

        Returns the combined result. If any hook disallows, the chain
        is short-circuited and that result is returned.
        """
        hooks = self.get_hooks(event)
        if not hooks:
            return HookResult(allow=True)

        combined_args: dict[str, Any] = {}
        for hook in hooks:
            try:
                result = await hook.handler(event=event, **kwargs)
                if result is None:
                    continue
                if not result.allow:
                    logger.info("Hook %s blocked event %s: %s", hook.name, event.value, result.reason)
                    return result
                if result.modified_args:
                    combined_args.update(result.modified_args)
            except Exception:
                logger.exception("Hook %s raised exception on event %s", hook.name, event.value)

        return HookResult(allow=True, modified_args=combined_args if combined_args else None)

    def clear(self) -> None:
        """Remove all hooks."""
        self._hooks.clear()
