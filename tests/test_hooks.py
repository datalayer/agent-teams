# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Tests for agent_teams.hooks module."""

from __future__ import annotations

import pytest

from agent_teams.hooks import Hook, HookEvent, HookRegistry, HookResult


class TestHookEvent:
    def test_all_events_exist(self):
        events = [
            HookEvent.PRE_TEAM_START,
            HookEvent.POST_TEAM_START,
            HookEvent.PRE_TEAM_STOP,
            HookEvent.POST_TEAM_STOP,
            HookEvent.PRE_MEMBER_START,
            HookEvent.POST_MEMBER_START,
            HookEvent.PRE_MEMBER_STOP,
            HookEvent.POST_MEMBER_STOP,
            HookEvent.PRE_TASK_ASSIGN,
            HookEvent.POST_TASK_ASSIGN,
            HookEvent.PRE_TASK_EXECUTE,
            HookEvent.POST_TASK_EXECUTE,
            HookEvent.ON_TASK_COMPLETE,
            HookEvent.ON_TASK_FAILED,
            HookEvent.ON_MEMBER_ERROR,
        ]
        assert len(events) == 15


class TestHookResult:
    def test_defaults(self):
        r = HookResult()
        assert r.allow is True
        assert r.reason == ""
        assert r.modified_args is None

    def test_block(self):
        r = HookResult(allow=False, reason="not allowed")
        assert r.allow is False

    def test_modified_args(self):
        r = HookResult(modified_args={"key": "value"})
        assert r.modified_args == {"key": "value"}


class TestHookRegistry:
    async def test_register_and_run(self):
        registry = HookRegistry()

        async def my_hook(**kwargs):
            return HookResult(allow=True)

        registry.register(HookEvent.PRE_TEAM_START, my_hook, name="test_hook")
        result = await registry.run(HookEvent.PRE_TEAM_START)
        assert result.allow is True

    async def test_decorator(self):
        registry = HookRegistry()

        @registry.on(HookEvent.POST_TEAM_START)
        async def on_start(**kwargs):
            return HookResult(allow=True, modified_args={"started": True})

        result = await registry.run(HookEvent.POST_TEAM_START)
        assert result.allow is True
        assert result.modified_args == {"started": True}

    async def test_blocking_hook(self):
        registry = HookRegistry()

        @registry.on(HookEvent.PRE_TASK_ASSIGN)
        async def block_assign(**kwargs):
            return HookResult(allow=False, reason="Blocked for testing")

        result = await registry.run(HookEvent.PRE_TASK_ASSIGN)
        assert result.allow is False
        assert "Blocked" in result.reason

    async def test_chain_short_circuits(self):
        """If a hook blocks, subsequent hooks should not run."""
        registry = HookRegistry()
        executed = []

        @registry.on(HookEvent.PRE_TASK_ASSIGN, priority=0)
        async def blocker(**kwargs):
            executed.append("blocker")
            return HookResult(allow=False, reason="Nope")

        @registry.on(HookEvent.PRE_TASK_ASSIGN, priority=1)
        async def second(**kwargs):
            executed.append("second")
            return HookResult(allow=True)

        result = await registry.run(HookEvent.PRE_TASK_ASSIGN)
        assert result.allow is False
        assert executed == ["blocker"]

    async def test_priority_ordering(self):
        registry = HookRegistry()
        order = []

        @registry.on(HookEvent.PRE_TEAM_START, priority=10)
        async def low_priority(**kwargs):
            order.append("low")
            return None

        @registry.on(HookEvent.PRE_TEAM_START, priority=1)
        async def high_priority(**kwargs):
            order.append("high")
            return None

        await registry.run(HookEvent.PRE_TEAM_START)
        assert order == ["high", "low"]

    async def test_unregister(self):
        registry = HookRegistry()

        @registry.on(HookEvent.PRE_TEAM_START)
        async def removable(**kwargs):
            return HookResult(allow=False, reason="Block")

        # Verify it blocks
        result = await registry.run(HookEvent.PRE_TEAM_START)
        assert result.allow is False

        # Unregister
        removed = registry.unregister(HookEvent.PRE_TEAM_START, "removable")
        assert removed is True

        # No longer blocks
        result = await registry.run(HookEvent.PRE_TEAM_START)
        assert result.allow is True

    async def test_unregister_not_found(self):
        registry = HookRegistry()
        assert registry.unregister(HookEvent.PRE_TEAM_START, "nope") is False

    async def test_no_hooks_returns_allow(self):
        registry = HookRegistry()
        result = await registry.run(HookEvent.PRE_TEAM_START)
        assert result.allow is True

    async def test_hook_exception_is_caught(self):
        registry = HookRegistry()

        @registry.on(HookEvent.PRE_TEAM_START)
        async def broken(**kwargs):
            raise RuntimeError("Oops")

        # Should not raise — exception is logged and skipped
        result = await registry.run(HookEvent.PRE_TEAM_START)
        assert result.allow is True

    async def test_get_hooks(self):
        registry = HookRegistry()

        @registry.on(HookEvent.ON_TASK_COMPLETE)
        async def h1(**kwargs):
            return None

        hooks = registry.get_hooks(HookEvent.ON_TASK_COMPLETE)
        assert len(hooks) == 1
        assert hooks[0].name == "h1"

    async def test_clear(self):
        registry = HookRegistry()

        @registry.on(HookEvent.ON_TASK_COMPLETE)
        async def h1(**kwargs):
            return None

        registry.clear()
        assert registry.get_hooks(HookEvent.ON_TASK_COMPLETE) == []

    async def test_combined_modified_args(self):
        registry = HookRegistry()

        @registry.on(HookEvent.PRE_TEAM_START, priority=0)
        async def first(**kwargs):
            return HookResult(modified_args={"a": 1})

        @registry.on(HookEvent.PRE_TEAM_START, priority=1)
        async def second(**kwargs):
            return HookResult(modified_args={"b": 2})

        result = await registry.run(HookEvent.PRE_TEAM_START)
        assert result.allow is True
        assert result.modified_args == {"a": 1, "b": 2}

    async def test_none_return_is_fine(self):
        registry = HookRegistry()

        @registry.on(HookEvent.ON_MEMBER_ERROR)
        async def returns_none(**kwargs):
            return None

        result = await registry.run(HookEvent.ON_MEMBER_ERROR)
        assert result.allow is True

    async def test_disabled_hook_skipped(self):
        registry = HookRegistry()

        async def should_block(**kwargs):
            return HookResult(allow=False, reason="block")

        registry.register(HookEvent.PRE_TEAM_START, should_block, name="blocker")
        # Disable it
        for h in registry._hooks.get(HookEvent.PRE_TEAM_START, []):
            h.enabled = False

        result = await registry.run(HookEvent.PRE_TEAM_START)
        assert result.allow is True
