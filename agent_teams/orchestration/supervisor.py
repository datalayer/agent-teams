# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Supervisor orchestration strategy.

The supervisor agent receives all tasks, decides how to route them
to team members, monitors progress, and aggregates results. This is
the primary orchestration pattern matching the UI's team model.

Inspired by:
- Gastown's Mayor-Enhanced Orchestration Workflow (MEOW)
- A2A's task delegation model
- Pydantic-AI's agent delegation pattern (agent-as-tool)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

from ..protocol.messages import (
    MessageType,
    TaskAssignment,
    TaskResultMessage,
)
from ..types import (
    AgentMemberState,
    EventType,
    MemberStatus,
    TaskDefinition,
    TaskResult,
    TaskStatus,
    TeamEvent,
)
from .base import BaseOrchestrator, MemberProxy

logger = logging.getLogger(__name__)


class SupervisorOrchestrator(BaseOrchestrator):
    """Supervisor-based orchestration.

    A supervisor agent (typically an LLM) receives the full task list,
    decides which member to assign each task to, and monitors progress.
    The supervisor can:

    - Route tasks based on member capabilities and roles.
    - Re-assign failed tasks with retries.
    - Split tasks into subtasks for parallel execution.
    - Aggregate results into a final output.

    If no explicit supervisor agent is configured, the orchestrator
    acts as a deterministic supervisor using role-based matching.
    """

    def __init__(self) -> None:
        super().__init__()
        self._supervisor_proxy: Optional[MemberProxy] = None
        self._pending_results: dict[str, asyncio.Future[TaskResult]] = {}

    async def _setup(self) -> None:
        """Register the supervisor on the channel and set up handlers."""
        await self.ctx.channel.register("supervisor")

        # Register handler for task results
        from ..protocol.channel import InMemoryChannel

        if isinstance(self.ctx.channel, InMemoryChannel):
            self.ctx.channel.add_handler(
                MessageType.TASK_RESULT,
                self._on_task_result_message,
            )

    async def execute(self, tasks: list[TaskDefinition]) -> list[TaskResult]:
        """Execute tasks through supervisor-mediated delegation.

        1. Add all tasks to the shared task list.
        2. For each available task, find the best member and assign.
        3. Wait for results and handle retries.
        4. Return collected results.
        """
        # Add tasks to shared list
        for task in tasks:
            await self.ctx.task_list.add(task)

        results: list[TaskResult] = []
        max_retries = self.ctx.config.validation.max_retries

        while True:
            # Get available (unblocked, pending) tasks
            available = self.ctx.task_list.get_available()
            if not available and not self._has_in_progress_tasks():
                break

            # Assign available tasks to idle members
            for task in available:
                assigned = await self.assign_task(task)
                if not assigned:
                    logger.debug("No idle member for task %s, will retry", task.id)

            # Wait for at least one result
            if self._has_in_progress_tasks():
                result = await self._wait_for_any_result(
                    timeout=self.ctx.config.validation.timeout_seconds,
                )
                if result:
                    await self.handle_result(result)
                    results.append(result)

                    # Handle retries for failed tasks
                    if result.status == TaskStatus.FAILED:
                        task = await self.ctx.task_list.get(result.task_id)
                        if task and task.retry_count < max_retries:
                            await self.ctx.task_list.reset(result.task_id)
                            logger.info(
                                "Retrying task %s (attempt %d/%d)",
                                result.task_id,
                                task.retry_count + 1,
                                max_retries,
                            )
            else:
                # No in-progress tasks and no available tasks — could be
                # a deadlock (circular dependencies) or all done
                if available:
                    # Tasks available but no members — wait briefly
                    await asyncio.sleep(1)
                else:
                    break

        return results

    async def assign_task(self, task: TaskDefinition) -> bool:
        """Assign a task to the best-suited idle member.

        Routing strategy:
        1. If ``task.assigned_to`` is set, respect the explicit assignment.
        2. Otherwise, match by role/capability.
        3. Fall back to round-robin among idle members.
        """
        # Explicit assignment
        if task.assigned_to:
            member = self.ctx.get_member(task.assigned_to)
            if member and member.status == MemberStatus.IDLE:
                return await self._dispatch_to_member(task, member)

        # Role-based matching
        idle_members = self.ctx.get_idle_members()
        if not idle_members:
            return False

        best_member = self._select_best_member(task, idle_members)
        if best_member is None:
            return False

        return await self._dispatch_to_member(task, best_member)

    async def handle_result(self, result: TaskResult) -> None:
        """Process a task result — update state and emit events."""
        task = await self.ctx.task_list.get(result.task_id)
        if task is None:
            logger.warning("Received result for unknown task %s", result.task_id)
            return

        # Update task state
        if result.status == TaskStatus.COMPLETED:
            await self.ctx.task_list.complete(result.task_id, result)
        elif result.status == TaskStatus.FAILED:
            await self.ctx.task_list.fail(result.task_id, result.error or "Unknown error")

        # Update member state
        member = self.ctx.get_member(result.member_id)
        if member:
            member.state.status = MemberStatus.IDLE
            member.state.current_task_id = None
            member.state.tokens_used += result.tokens_used
            if result.status == TaskStatus.COMPLETED:
                member.state.tasks_completed += 1
            else:
                member.state.tasks_failed += 1

        # Store artifacts
        for artifact in result.artifacts:
            from ..types import Artifact

            art = Artifact(
                name=artifact.get("name", "artifact"),
                content_type=artifact.get("content_type", "text/plain"),
                content=artifact.get("content"),
                uri=artifact.get("uri"),
            )
            await self.ctx.artifact_store.store(self.ctx.config.id, art)

        # Emit event
        event_type = (
            EventType.TASK_COMPLETED
            if result.status == TaskStatus.COMPLETED
            else EventType.TASK_FAILED
        )
        self.ctx.events.append(
            TeamEvent(
                type=event_type,
                source=result.member_id,
                message=f"Task '{task.title}' {result.status.value} by {result.member_id}",
                data={"task_id": result.task_id, "tokens_used": result.tokens_used},
            )
        )

        # Resolve pending future if any
        future = self._pending_results.pop(result.task_id, None)
        if future and not future.done():
            future.set_result(result)

    async def shutdown(self) -> None:
        """Unregister supervisor and cancel pending futures."""
        if self._ctx:
            await self.ctx.channel.unregister("supervisor")
        for future in self._pending_results.values():
            if not future.done():
                future.cancel()
        self._pending_results.clear()
        await super().shutdown()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _select_best_member(
        self,
        task: TaskDefinition,
        candidates: list[MemberProxy],
    ) -> Optional[MemberProxy]:
        """Select the best member for a task based on role matching.

        Scoring:
        - +10 if the member's role matches the task metadata ``role`` key.
        - +5 if the member has relevant tools.
        - +1 base score (round-robin tiebreaker: fewest completed tasks).
        """
        if not candidates:
            return None

        desired_role = task.metadata.get("role", "").lower()
        desired_tools = set(task.metadata.get("tools", []))

        scored: list[tuple[int, MemberProxy]] = []
        for member in candidates:
            score = 0
            if desired_role and member.config.role.lower() == desired_role:
                score += 10
            member_tools = set(member.config.tools)
            if desired_tools and desired_tools & member_tools:
                score += 5
            # Prefer members with fewer completed tasks (load balancing)
            score -= member.state.tasks_completed
            scored.append((score, member))

        scored.sort(key=lambda x: -x[0])
        return scored[0][1]

    async def _dispatch_to_member(
        self,
        task: TaskDefinition,
        member: MemberProxy,
    ) -> bool:
        """Send a task to a member and update state."""
        # Claim the task
        claimed = await self.ctx.task_list.claim(task.id, member.id)
        if not claimed:
            return False

        # Update member state
        member.state.status = MemberStatus.RUNNING
        member.state.current_task_id = task.id

        # Create a future for the result
        loop = asyncio.get_running_loop()
        future: asyncio.Future[TaskResult] = loop.create_future()
        self._pending_results[task.id] = future

        # Send assignment via channel
        assignment = TaskAssignment(
            team_id=self.ctx.config.id,
            sender="supervisor",
            recipient=member.id,
            task_id=task.id,
            title=task.title,
            description=task.description,
            priority=task.priority,
            input_data=task.input_data,
            timeout_seconds=self.ctx.config.validation.timeout_seconds,
        )
        await self.ctx.channel.send(assignment)

        # Mark task as in-progress
        await self.ctx.task_list.start(task.id)

        # Emit event
        self.ctx.events.append(
            TeamEvent(
                type=EventType.TASK_ASSIGNED,
                source="supervisor",
                message=f"Task '{task.title}' assigned to {member.name}",
                data={"task_id": task.id, "member_id": member.id},
            )
        )

        # Run the member agent asynchronously
        asyncio.create_task(self._run_member_task(member, task))

        return True

    async def _run_member_task(
        self,
        member: MemberProxy,
        task: TaskDefinition,
    ) -> None:
        """Execute a task on a member agent and report the result."""
        start_time = datetime.utcnow()
        try:
            prompt = self._build_task_prompt(task)
            output = await member.run(prompt, {"task_id": task.id, **task.input_data})

            duration = (datetime.utcnow() - start_time).total_seconds()
            result = TaskResult(
                task_id=task.id,
                member_id=member.id,
                status=TaskStatus.COMPLETED,
                output=output,
                duration_seconds=duration,
            )
        except Exception as exc:
            duration = (datetime.utcnow() - start_time).total_seconds()
            result = TaskResult(
                task_id=task.id,
                member_id=member.id,
                status=TaskStatus.FAILED,
                error=str(exc),
                duration_seconds=duration,
            )
            logger.exception("Task %s failed on member %s", task.id, member.id)

        # Report result back via channel
        result_msg = TaskResultMessage(
            team_id=self.ctx.config.id,
            sender=member.id,
            recipient="supervisor",
            task_id=task.id,
            status=result.status,
            output=result.output,
            error=result.error,
            tokens_used=result.tokens_used,
            duration_seconds=result.duration_seconds,
        )
        await self.ctx.channel.send(result_msg)

        # Also handle directly (in case channel handler doesn't fire)
        await self.handle_result(result)

    def _build_task_prompt(self, task: TaskDefinition) -> str:
        """Build a prompt for the member agent from a task definition."""
        parts = [f"# Task: {task.title}"]
        if task.description:
            parts.append(f"\n{task.description}")
        if task.input_data:
            parts.append(f"\n## Input Data\n```json\n{task.input_data}\n```")
        if task.metadata:
            parts.append(f"\n## Context\n```json\n{task.metadata}\n```")
        return "\n".join(parts)

    def _has_in_progress_tasks(self) -> bool:
        """Check if any tasks are currently being executed."""
        from ..types import TaskStatus

        in_progress = self.ctx.task_list.get_by_status(TaskStatus.IN_PROGRESS)
        assigned = self.ctx.task_list.get_by_status(TaskStatus.ASSIGNED)
        return bool(in_progress or assigned)

    async def _wait_for_any_result(self, timeout: float) -> Optional[TaskResult]:
        """Wait for any pending task to complete."""
        if not self._pending_results:
            return None

        futures = list(self._pending_results.values())
        try:
            done, _ = await asyncio.wait(
                futures,
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if done:
                return done.pop().result()
        except Exception:
            logger.exception("Error waiting for task results")

        return None

    async def _on_task_result_message(self, message: Any) -> None:
        """Handle task result messages from the channel."""
        if isinstance(message, TaskResultMessage):
            result = TaskResult(
                task_id=message.task_id,
                member_id=message.sender,
                status=message.status,
                output=message.output,
                error=message.error,
                tokens_used=message.tokens_used,
                duration_seconds=message.duration_seconds,
            )
            await self.handle_result(result)
