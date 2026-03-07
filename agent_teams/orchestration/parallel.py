# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Parallel orchestration strategy.

Tasks are dispatched to all available members concurrently.
Dependency ordering is still respected — only unblocked tasks
are dispatched. Failed tasks are retried up to ``max_retries``.
"""

from __future__ import annotations

import asyncio
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


class ParallelOrchestrator(BaseOrchestrator):
    """Execute tasks in parallel across available members.

    All unblocked tasks are dispatched to idle members simultaneously.
    When a member finishes, the next available task is dispatched.
    This is similar to gastown's capacity-controlled dispatch with
    ``MaxPolecats > 1``.
    """

    async def execute(self, tasks: list[TaskDefinition]) -> list[TaskResult]:
        """Execute tasks in parallel."""
        for task in tasks:
            await self.ctx.task_list.add(task)

        results: list[TaskResult] = []
        max_retries = self.ctx.config.validation.max_retries
        running_tasks: dict[str, asyncio.Task[TaskResult]] = {}

        while True:
            # Dispatch available tasks to idle members
            available = self.ctx.task_list.get_available()
            idle_members = self.ctx.get_idle_members()

            for task in available:
                if not idle_members:
                    break

                member = idle_members.pop(0)
                claimed = await self.ctx.task_list.claim(task.id, member.id)
                if not claimed:
                    continue

                member.state.status = MemberStatus.RUNNING
                member.state.current_task_id = task.id
                await self.ctx.task_list.start(task.id)

                # Launch async task
                coro = self._run_member_task(member, task)
                async_task = asyncio.create_task(coro)
                running_tasks[task.id] = async_task

            if not running_tasks:
                # No running tasks and no available tasks — done
                break

            # Wait for at least one to complete
            done, _ = await asyncio.wait(
                running_tasks.values(),
                timeout=self.ctx.config.validation.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )

            for completed_task in done:
                result = completed_task.result()
                results.append(result)
                await self.handle_result(result)
                running_tasks.pop(result.task_id, None)

                # Retry logic
                if result.status == TaskStatus.FAILED:
                    task_def = await self.ctx.task_list.get(result.task_id)
                    if task_def and task_def.retry_count < max_retries:
                        await self.ctx.task_list.reset(result.task_id)

            if not done and running_tasks:
                # Timeout — cancel remaining and report failures
                for task_id, async_task in running_tasks.items():
                    async_task.cancel()
                    results.append(
                        TaskResult(
                            task_id=task_id,
                            member_id="",
                            status=TaskStatus.FAILED,
                            error="Timeout exceeded",
                        )
                    )
                    await self.ctx.task_list.fail(task_id, "Timeout exceeded")
                running_tasks.clear()

        return results

    async def assign_task(self, task: TaskDefinition) -> bool:
        """Assign task to the first idle member."""
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
                message=f"Task {result.status.value}",
                data={"task_id": result.task_id},
            )
        )

    async def _run_member_task(
        self,
        member: MemberProxy,
        task: TaskDefinition,
    ) -> TaskResult:
        """Execute a task on a member and return the result."""
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
            return TaskResult(
                task_id=task.id,
                member_id=member.id,
                status=TaskStatus.FAILED,
                error=str(exc),
                duration_seconds=duration,
            )
