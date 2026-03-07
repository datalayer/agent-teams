# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Tests for agent_teams.reactions module."""

from __future__ import annotations

from typing import Any

import pytest

from agent_teams.reactions import (
    ReactionAction,
    ReactionConfig,
    ReactionEngine,
    ReactionPriority,
    ReactionState,
    ReactionTrigger,
)


class TestReactionEnums:
    def test_action_values(self):
        assert ReactionAction.SEND_TO_AGENT == "send-to-agent"
        assert ReactionAction.REASSIGN == "reassign"
        assert ReactionAction.RESTART_MEMBER == "restart-member"
        assert ReactionAction.NOTIFY == "notify"
        assert ReactionAction.AUTO_MERGE == "auto-merge"
        assert ReactionAction.CUSTOM == "custom"

    def test_priority_values(self):
        assert ReactionPriority.INFO == "info"
        assert ReactionPriority.WARNING == "warning"
        assert ReactionPriority.ACTION == "action"
        assert ReactionPriority.URGENT == "urgent"

    def test_trigger_values(self):
        assert ReactionTrigger.TASK_FAILED == "task-failed"
        assert ReactionTrigger.TASK_STUCK == "task-stuck"
        assert ReactionTrigger.MEMBER_UNRESPONSIVE == "member-unresponsive"
        assert ReactionTrigger.MEMBER_STUCK == "member-stuck"
        assert ReactionTrigger.MEMBER_DEAD == "member-dead"
        assert ReactionTrigger.ALL_COMPLETE == "all-complete"
        assert ReactionTrigger.MASS_DEATH == "mass-death"


class TestReactionConfig:
    def test_defaults(self):
        rc = ReactionConfig(
            trigger=ReactionTrigger.TASK_FAILED,
            action=ReactionAction.NOTIFY,
        )
        assert rc.auto is True
        assert rc.priority == ReactionPriority.WARNING
        assert rc.max_retries == 2
        assert rc.escalate_after_retries == 2
        assert rc.escalate_after_seconds == 1800

    def test_custom_values(self):
        rc = ReactionConfig(
            trigger=ReactionTrigger.MEMBER_DEAD,
            action=ReactionAction.RESTART_MEMBER,
            auto=True,
            priority=ReactionPriority.URGENT,
            max_retries=3,
        )
        assert rc.max_retries == 3
        assert rc.priority == ReactionPriority.URGENT


class TestReactionState:
    def test_defaults(self):
        rs = ReactionState(
            trigger=ReactionTrigger.TASK_FAILED,
            target_id="task-1",
        )
        assert rs.attempt_count == 0
        assert rs.escalated is False
        assert rs.resolved is False


class TestReactionEngine:
    def test_add_rule(self):
        engine = ReactionEngine()
        engine.add_rule(ReactionConfig(
            trigger=ReactionTrigger.TASK_FAILED,
            action=ReactionAction.NOTIFY,
        ))
        assert len(engine._rules) == 1

    async def test_fire_no_matching_rules(self):
        engine = ReactionEngine()
        # No rules -> fire() does nothing and doesn't crash
        result = await engine.fire(ReactionTrigger.TASK_FAILED, "task-1")
        assert result is False

    async def test_fire_matching_rule_calls_notify(self):
        engine = ReactionEngine()
        engine.add_rule(ReactionConfig(
            trigger=ReactionTrigger.TASK_FAILED,
            action=ReactionAction.NOTIFY,
            message_template="Task {target_id} failed",
        ))
        notifications: list[tuple[str, str]] = []

        async def notify_handler(**kwargs: Any) -> None:
            notifications.append((kwargs.get("message", ""), kwargs.get("priority", "")))

        engine.set_notify_handler(notify_handler)
        result = await engine.fire(ReactionTrigger.TASK_FAILED, "task-1")
        assert result is True
        assert len(notifications) == 1

    async def test_fire_send_to_agent(self):
        engine = ReactionEngine()
        engine.add_rule(ReactionConfig(
            trigger=ReactionTrigger.MEMBER_STUCK,
            action=ReactionAction.SEND_TO_AGENT,
            message_template="Fix stuck member {target_id}",
        ))
        sent: list[str] = []

        async def send_handler(**kwargs: Any) -> None:
            sent.append(kwargs.get("target_id", ""))

        engine.set_send_handler(send_handler)
        await engine.fire(ReactionTrigger.MEMBER_STUCK, "m1")
        assert len(sent) == 1
        assert sent[0] == "m1"

    async def test_fire_reassign(self):
        engine = ReactionEngine()
        engine.add_rule(ReactionConfig(
            trigger=ReactionTrigger.MEMBER_DEAD,
            action=ReactionAction.REASSIGN,
        ))
        reassigned: list[str] = []

        async def reassign_handler(**kwargs: Any) -> None:
            reassigned.append(kwargs.get("target_id", ""))

        engine.set_reassign_handler(reassign_handler)
        await engine.fire(ReactionTrigger.MEMBER_DEAD, "m2")
        assert "m2" in reassigned

    async def test_fire_restart_member(self):
        engine = ReactionEngine()
        engine.add_rule(ReactionConfig(
            trigger=ReactionTrigger.MEMBER_UNRESPONSIVE,
            action=ReactionAction.RESTART_MEMBER,
        ))
        restarted: list[str] = []

        async def restart_handler(**kwargs: Any) -> None:
            restarted.append(kwargs.get("target_id", ""))

        engine.set_restart_handler(restart_handler)
        await engine.fire(ReactionTrigger.MEMBER_UNRESPONSIVE, "m3")
        assert "m3" in restarted

    async def test_retry_logic(self):
        engine = ReactionEngine()
        engine.add_rule(ReactionConfig(
            trigger=ReactionTrigger.TASK_FAILED,
            action=ReactionAction.NOTIFY,
            max_retries=2,
        ))
        calls: list[str] = []

        async def notify_handler(**kwargs: Any) -> None:
            calls.append(kwargs.get("message", ""))

        engine.set_notify_handler(notify_handler)

        await engine.fire(ReactionTrigger.TASK_FAILED, "t1")
        await engine.fire(ReactionTrigger.TASK_FAILED, "t1")
        await engine.fire(ReactionTrigger.TASK_FAILED, "t1")
        # Max retries = 2 means 3 total fires (initial + 2 retries)
        assert len(calls) == 3

    async def test_resolve(self):
        engine = ReactionEngine()
        engine.add_rule(ReactionConfig(
            trigger=ReactionTrigger.TASK_FAILED,
            action=ReactionAction.NOTIFY,
            max_retries=5,
        ))
        calls: list[str] = []

        async def notify_handler(**kwargs: Any) -> None:
            calls.append(kwargs.get("message", ""))

        engine.set_notify_handler(notify_handler)

        await engine.fire(ReactionTrigger.TASK_FAILED, "t1")
        engine.resolve(ReactionTrigger.TASK_FAILED, "t1")
        # After resolve, fire returns False (state.resolved=True)
        result = await engine.fire(ReactionTrigger.TASK_FAILED, "t1")
        assert result is False
        assert len(calls) == 1

    async def test_escalation_after_retries(self):
        engine = ReactionEngine()
        engine.add_rule(ReactionConfig(
            trigger=ReactionTrigger.TASK_FAILED,
            action=ReactionAction.NOTIFY,
            max_retries=10,
            escalate_after_retries=2,
            priority=ReactionPriority.WARNING,
        ))
        priorities: list = []

        async def notify_handler(**kwargs: Any) -> None:
            priorities.append(kwargs.get("priority"))

        engine.set_notify_handler(notify_handler)

        await engine.fire(ReactionTrigger.TASK_FAILED, "t1")
        await engine.fire(ReactionTrigger.TASK_FAILED, "t1")
        await engine.fire(ReactionTrigger.TASK_FAILED, "t1")  # 3rd fire → escalate_after_retries=2

        # First 2 fires: WARNING, 3rd: should be escalated to URGENT
        assert priorities[0] == ReactionPriority.WARNING
        assert priorities[-1] == ReactionPriority.URGENT

    async def test_custom_handler(self):
        custom_calls: list[str] = []
        engine = ReactionEngine()

        async def custom_fn(**kwargs: Any) -> None:
            custom_calls.append(kwargs.get("target_id", ""))

        engine.add_rule(ReactionConfig(
            trigger=ReactionTrigger.ALL_COMPLETE,
            action=ReactionAction.CUSTOM,
            custom_handler=custom_fn,
        ))
        await engine.fire(ReactionTrigger.ALL_COMPLETE, "team-1")
        assert "team-1" in custom_calls
