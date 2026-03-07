# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Sequential orchestration strategy.

Tasks are executed one at a time, in priority/dependency order.
Each task must complete (or fail after retries) before the next
task is dispatched. This is the simplest strategy.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from ..types import (
    EventType,
    MemberStatus,
    TaskDefinition,
    TaskResult,
    TaskStatus,
    TeamEvent,
)
from .base import BaseOrchestrator, MemberProxy

logger = logging.getLogger(__name__)


class SequentialOrchestrator(BaseOrchestrator):
    """Execute tasks one at a time in priority order.

    For each task:
    1. Find the best member (role match, then round-robin).
    2. Execute the task on that member.
    3. Collect the result.
    4. Move to the next task.

    If a task fails, it is retried up to ``max_retries`` times
    before being marked as permanently failed.
    """

    async def execute(self, tasks: list[TaskDefinition]) -> list[TaskResult]:
        """Execute tasks sequentially."""
        for task in tasks:
            await self.ctx.task_list.add(task)

        results: list[TaskResult] = []
        max_retries = self.ctx.config.validation.max_retries

        while True:
            available = self.ctx.task_list.get_available()
            if not available:
                break

            task = available[0]
            result = await self._execute_single_task(task, max_retries)
            results.append(result)
            await self.handle_result(result)

        return results

    async def assign_task(self, task: TaskDefinition) -> bool:
        """Assign to the first available idle member."""
        idle = self.ctx.get_idle_members()
        if not idle:
            return False

        member = idle[0]
        claimed = await self.ctx.task_list.claim(task.id, member.id)
        if not claimed:
            return False

        member.state.status = MemberStatus.RUNNING
        member.state.current_task_id = task.id
        return True

    async def handle_result(self, result: TaskResult) -> None:
        """Update state after a task completes."""
        if result.status == TaskStatus.COMPLETED:
            await self.ctx.task_list.complete(result.task_id, result)
        elif result.status == TaskStatus.FAILED:
            await self.ctx.task_list.fail(result.task_id, result.error or "Unknown error")

        member = self.ctx.get_member(result.member_id)
        if member:
            member.state.status = MemberStatus.IDLE
            member.state.current_task_id = None
            member.state.tokens_used += result.tokens_used
            if result.status == TaskStatus.COMPLETED:
                member.state.tasks_completed += 1
            else:
                member.state.tasks_failed += 1

        event_type = (
            EventType.TASK_COMPLETED
            if result.status == TaskStatus.COMPLETED
            else EventType.TASK_FAILED
        )
        self.ctx.events.append(
            TeamEvent(
                type=event_type,
                source=result.member_id,
                message=f"Task completed: {result.status.value}",
                data={"task_id": result.task_id},
            )
        )

    async def _execute_single_task(
        self,
        task: TaskDefinition,
        max_retries: int,
    ) -> TaskResult:
        """Execute a single task with retries."""
        for attempt in range(max_retries + 1):
            idle = self.ctx.get_idle_members()
            if not idle:
                return TaskResult(
                    task_id=task.id,
                    member_id="",
                    status=TaskStatus.FAILED,
                    error="No idle members available",
                )

            member = idle[0]
            if attempt > 0:
                # Reset the task for retry
                await self.ctx.task_list.reset(task.id)

            claimed = await self.ctx.task_list.claim(task.id, member.id)
            if not claimed:
                continue

            member.state.status = MemberStatus.RUNNING
            member.state.current_task_id = task.id
            await self.ctx.task_list.start(task.id)

            start_time = datetime.utcnow()
            try:
                prompt = f"# Task: {task.title}\n\n{task.description}"
                output = await member.run(prompt, task.input_data)
                duration = (datetime.utcnow() - start_time).total_seconds()
                return TaskResult(
                    task_id=task.id,
                    member_id=member.id,
                    status=TaskStatus.COMPLETED,
                    output=output,
                    duration_seconds=duration,
                )
            except Exception as exc:
                duration = (datetime.utcnow() - start_time).total_seconds()
                logger.warning(
                    "Task %s attempt %d failed: %s",
                    task.id,
                    attempt + 1,
                    exc,
                )
                result = TaskResult(
                    task_id=task.id,
                    member_id=member.id,
                    status=TaskStatus.FAILED,
                    error=str(exc),
                    duration_seconds=duration,
                )
                member.state.status = MemberStatus.IDLE
                member.state.current_task_id = None

                if attempt == max_retries:
                    return result

        # Should not reach here
        return TaskResult(
            task_id=task.id,
            member_id="",
            status=TaskStatus.FAILED,
            error="Exhausted all retries",
        )
