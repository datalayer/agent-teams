# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Shared task list for coordinating work across team members.

Inspired by pydantic-deepagents' ``SharedTodoList`` and gastown's
convoy-based dependency-aware dispatching. Tasks support:

- Priority-based ordering
- Dependency tracking (a task can depend on other tasks)
- Atomic claiming (only one member can claim a task at a time)
- Blocker-aware filtering (blocked tasks are not dispatchable)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from ..types import TaskDefinition, TaskPriority, TaskResult, TaskStatus

logger = logging.getLogger(__name__)


class SharedTaskList:
    """Thread-safe shared task list with dependency-aware scheduling.

    Example::

        task_list = SharedTaskList()
        task_list.add(TaskDefinition(title="Analyze data", priority=TaskPriority.HIGH))
        available = task_list.get_available()
        claimed = task_list.claim(available[0].id, "agent-1")
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TaskDefinition] = {}
        self._lock = asyncio.Lock()

    @property
    def tasks(self) -> list[TaskDefinition]:
        """All tasks sorted by priority."""
        return sorted(self._tasks.values(), key=lambda t: (t.priority.value, t.created_at))

    async def add(self, task: TaskDefinition) -> TaskDefinition:
        """Add a task to the list."""
        async with self._lock:
            self._tasks[task.id] = task
            logger.debug("Task %s added: %s", task.id, task.title)
            return task

    async def get(self, task_id: str) -> Optional[TaskDefinition]:
        """Get a task by ID."""
        return self._tasks.get(task_id)

    async def remove(self, task_id: str) -> bool:
        """Remove a task from the list."""
        async with self._lock:
            return self._tasks.pop(task_id, None) is not None

    def get_available(self) -> list[TaskDefinition]:
        """Return tasks that are unblocked, unclaimed, and pending.

        A task is blocked if any of its ``depends_on`` tasks are not completed.
        Results are sorted by priority (lower value = higher priority).
        """
        completed_ids = {
            tid for tid, t in self._tasks.items() if t.status == TaskStatus.COMPLETED
        }
        available = []
        for task in self._tasks.values():
            if task.status != TaskStatus.PENDING:
                continue
            # Check dependencies
            blocked = any(
                dep_id not in completed_ids for dep_id in task.depends_on
            )
            if blocked:
                continue
            available.append(task)
        return sorted(available, key=lambda t: (t.priority.value, t.created_at))

    async def claim(self, task_id: str, member_id: str) -> bool:
        """Atomically claim a task for a member.

        Returns ``True`` if successfully claimed, ``False`` if the task
        is not available (already claimed, blocked, or not found).
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            if task.status != TaskStatus.PENDING:
                return False

            # Check dependencies
            completed_ids = {
                tid for tid, t in self._tasks.items() if t.status == TaskStatus.COMPLETED
            }
            if any(dep_id not in completed_ids for dep_id in task.depends_on):
                return False

            task.status = TaskStatus.ASSIGNED
            task.assigned_to = member_id
            task.started_at = datetime.utcnow()
            logger.debug("Task %s claimed by %s", task_id, member_id)
            return True

    async def start(self, task_id: str) -> bool:
        """Mark a task as in-progress."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status != TaskStatus.ASSIGNED:
                return False
            task.status = TaskStatus.IN_PROGRESS
            task.started_at = task.started_at or datetime.utcnow()
            return True

    async def complete(self, task_id: str, result: TaskResult) -> bool:
        """Mark a task as completed with a result."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            if task.status not in (TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS):
                return False
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.utcnow()
            task.result = result
            logger.debug("Task %s completed by %s", task_id, task.assigned_to)
            return True

    async def fail(self, task_id: str, error: str) -> bool:
        """Mark a task as failed."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            task.status = TaskStatus.FAILED
            task.completed_at = datetime.utcnow()
            task.result = TaskResult(
                task_id=task_id,
                member_id=task.assigned_to or "",
                status=TaskStatus.FAILED,
                error=error,
            )
            logger.debug("Task %s failed: %s", task_id, error)
            return True

    async def cancel(self, task_id: str) -> bool:
        """Cancel a task."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
                return False
            task.status = TaskStatus.CANCELLED
            task.completed_at = datetime.utcnow()
            return True

    async def reset(self, task_id: str) -> bool:
        """Reset a failed task back to pending for retry."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            if task.status != TaskStatus.FAILED:
                return False
            task.status = TaskStatus.PENDING
            task.assigned_to = None
            task.started_at = None
            task.completed_at = None
            task.result = None
            task.retry_count += 1
            return True

    def get_by_member(self, member_id: str) -> list[TaskDefinition]:
        """Get all tasks assigned to a specific member."""
        return [
            t
            for t in self._tasks.values()
            if t.assigned_to == member_id
        ]

    def get_by_status(self, status: TaskStatus) -> list[TaskDefinition]:
        """Get all tasks with a given status."""
        return [t for t in self._tasks.values() if t.status == status]

    @property
    def stats(self) -> dict[str, int]:
        """Task count by status."""
        counts: dict[str, int] = {}
        for task in self._tasks.values():
            key = task.status.value
            counts[key] = counts.get(key, 0) + 1
        return counts
