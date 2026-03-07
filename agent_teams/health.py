# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Health monitoring for agent team members.

Implements heartbeat-based liveness detection, stuck agent
identification, and automatic recovery — inspired by:

- Gastown's Daemon heartbeat loop (15-step health check sequence)
- Gastown's GUPP (agents must make progress on hooked work)
- Agent-orchestrator's LifecycleManager (polling-based state machine)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Health status model
# ---------------------------------------------------------------------------


class HealthState(str, Enum):
    """Health status of an agent member."""

    HEALTHY = "healthy"
    STALE = "stale"  # Heartbeat overdue but within grace
    UNRESPONSIVE = "unresponsive"  # Heartbeat critically overdue
    STUCK = "stuck"  # Active but not making progress
    DEAD = "dead"  # Session/process no longer running


@dataclass
class MemberHealth:
    """Health data for a single member."""

    member_id: str
    state: HealthState = HealthState.HEALTHY
    last_heartbeat: float = field(default_factory=time.time)
    last_progress: float = field(default_factory=time.time)
    consecutive_failures: int = 0
    tasks_since_last_check: int = 0
    restart_count: int = 0
    last_restart: Optional[float] = None


@dataclass
class HealthConfig:
    """Configuration for health monitoring."""

    heartbeat_interval: float = 30.0  # seconds between expected heartbeats
    stale_threshold: float = 90.0  # stale after this many seconds
    unresponsive_threshold: float = 300.0  # unresponsive after this
    stuck_threshold: float = 600.0  # stuck if no progress for this long
    max_restart_attempts: int = 3
    backoff_base: float = 2.0  # exponential backoff base for restarts
    check_interval: float = 15.0  # how often to run the health check loop


class HealthMonitor:
    """Monitors the health of all members in a team.

    Runs a periodic check loop that:
    1. Evaluates heartbeat freshness per member.
    2. Detects stuck agents (active but no task progress).
    3. Triggers recovery actions (restart, escalation).
    4. Detects mass death (multiple simultaneous failures).

    Usage::

        monitor = HealthMonitor(config=HealthConfig())
        monitor.register("member-1")
        await monitor.start()
        ...
        monitor.record_heartbeat("member-1")
        monitor.record_progress("member-1")
        ...
        await monitor.stop()
    """

    def __init__(
        self,
        config: HealthConfig | None = None,
        on_stale: Any | None = None,
        on_unresponsive: Any | None = None,
        on_stuck: Any | None = None,
        on_dead: Any | None = None,
        on_mass_death: Any | None = None,
    ) -> None:
        self._config = config or HealthConfig()
        self._members: dict[str, MemberHealth] = {}
        self._task: Optional[asyncio.Task[None]] = None
        self._running = False

        # Callbacks — synchronous callable(member_id: str) -> None
        self.on_stale = on_stale
        self.on_unresponsive = on_unresponsive
        self.on_stuck = on_stuck
        self.on_dead = on_dead
        self.on_mass_death = on_mass_death

        # Mass death tracking (gastown pattern: 3 deaths in 30s)
        self._death_timestamps: list[float] = []
        self._mass_death_window = 30.0
        self._mass_death_threshold = 3

    def register(self, member_id: str) -> None:
        """Register a member for health tracking."""
        self._members[member_id] = MemberHealth(member_id=member_id)

    def unregister(self, member_id: str) -> None:
        """Remove a member from health tracking."""
        self._members.pop(member_id, None)

    def record_heartbeat(self, member_id: str) -> None:
        """Record a heartbeat from a member."""
        if member_id in self._members:
            self._members[member_id].last_heartbeat = time.time()
            self._members[member_id].consecutive_failures = 0
            if self._members[member_id].state in (HealthState.STALE, HealthState.UNRESPONSIVE):
                self._members[member_id].state = HealthState.HEALTHY
                logger.info("Member %s recovered (heartbeat received)", member_id)

    def record_progress(self, member_id: str) -> None:
        """Record that a member made progress (completed task, etc.)."""
        if member_id in self._members:
            self._members[member_id].last_progress = time.time()
            self._members[member_id].tasks_since_last_check += 1
            if self._members[member_id].state == HealthState.STUCK:
                self._members[member_id].state = HealthState.HEALTHY
                logger.info("Member %s unstuck (progress detected)", member_id)

    def get_health(self, member_id: str) -> Optional[MemberHealth]:
        """Get the current health of a member."""
        return self._members.get(member_id)

    def get_all_health(self) -> dict[str, MemberHealth]:
        """Get health data for all members."""
        return dict(self._members)

    def get_unhealthy(self) -> list[MemberHealth]:
        """Get members that are not healthy."""
        return [m for m in self._members.values() if m.state != HealthState.HEALTHY]

    async def start(self) -> None:
        """Start the health check loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._check_loop())
        logger.info("Health monitor started (interval=%.1fs)", self._config.check_interval)

    async def stop(self) -> None:
        """Stop the health check loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Health monitor stopped")

    async def check_once(self) -> list[MemberHealth]:
        """Run a single health check cycle. Returns unhealthy members."""
        now = time.time()
        unhealthy: list[MemberHealth] = []
        newly_dead: list[str] = []

        for member in self._members.values():
            old_state = member.state
            elapsed = now - member.last_heartbeat
            progress_elapsed = now - member.last_progress

            if elapsed > self._config.unresponsive_threshold:
                member.state = HealthState.UNRESPONSIVE
                member.consecutive_failures += 1
            elif elapsed > self._config.stale_threshold:
                member.state = HealthState.STALE
            elif progress_elapsed > self._config.stuck_threshold and member.tasks_since_last_check == 0:
                member.state = HealthState.STUCK
            else:
                member.state = HealthState.HEALTHY

            # Reset progress counter
            member.tasks_since_last_check = 0

            # Check for state transitions
            if member.state != old_state:
                logger.info(
                    "Member %s: %s -> %s", member.member_id, old_state.value, member.state.value
                )
                if member.state == HealthState.STALE and self.on_stale:
                    self.on_stale(member.member_id)
                elif member.state == HealthState.UNRESPONSIVE and self.on_unresponsive:
                    self.on_unresponsive(member.member_id)
                elif member.state == HealthState.STUCK and self.on_stuck:
                    self.on_stuck(member.member_id)

                # Check if consecutive failures indicate death
                if member.consecutive_failures >= 3:
                    member.state = HealthState.DEAD
                    newly_dead.append(member.member_id)
                    if self.on_dead:
                        self.on_dead(member.member_id)

            if member.state != HealthState.HEALTHY:
                unhealthy.append(member)

        # Mass death detection (gastown pattern)
        if newly_dead:
            self._death_timestamps.extend([now] * len(newly_dead))
            # Prune old timestamps
            self._death_timestamps = [t for t in self._death_timestamps if now - t < self._mass_death_window]
            if len(self._death_timestamps) >= self._mass_death_threshold and self.on_mass_death:
                self.on_mass_death(newly_dead)

        return unhealthy

    def can_restart(self, member_id: str) -> bool:
        """Check if a member can be restarted (respecting backoff)."""
        member = self._members.get(member_id)
        if not member:
            return False
        if member.restart_count >= self._config.max_restart_attempts:
            return False
        if member.last_restart:
            backoff = self._config.backoff_base ** member.restart_count
            if time.time() - member.last_restart < backoff:
                return False
        return True

    def record_restart(self, member_id: str) -> None:
        """Record that a member was restarted."""
        if member_id in self._members:
            self._members[member_id].restart_count += 1
            self._members[member_id].last_restart = time.time()
            self._members[member_id].state = HealthState.HEALTHY
            self._members[member_id].last_heartbeat = time.time()
            self._members[member_id].consecutive_failures = 0

    async def _check_loop(self) -> None:
        """Periodic health check loop."""
        while self._running:
            try:
                await self.check_once()
            except Exception:
                logger.exception("Error in health check loop")
            await asyncio.sleep(self._config.check_interval)
