# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Tests for agent_teams.health module."""

from __future__ import annotations

import asyncio
import time

import pytest

from agent_teams.health import HealthConfig, HealthMonitor, HealthState, MemberHealth


class TestHealthState:
    def test_enum_values(self):
        assert HealthState.HEALTHY == "healthy"
        assert HealthState.STALE == "stale"
        assert HealthState.UNRESPONSIVE == "unresponsive"
        assert HealthState.STUCK == "stuck"
        assert HealthState.DEAD == "dead"


class TestMemberHealth:
    def test_defaults(self):
        mh = MemberHealth(member_id="m1")
        assert mh.state == HealthState.HEALTHY
        assert mh.consecutive_failures == 0
        assert mh.tasks_since_last_check == 0
        assert mh.restart_count == 0

    def test_custom_values(self):
        mh = MemberHealth(
            member_id="m2",
            state=HealthState.STUCK,
            consecutive_failures=3,
        )
        assert mh.state == HealthState.STUCK
        assert mh.consecutive_failures == 3


class TestHealthConfig:
    def test_defaults(self):
        c = HealthConfig()
        assert c.heartbeat_interval == 30.0
        assert c.stale_threshold == 90.0
        assert c.unresponsive_threshold == 300.0
        assert c.stuck_threshold == 600.0
        assert c.max_restart_attempts == 3
        assert c.backoff_base == 2.0
        assert c.check_interval == 15.0


class TestHealthMonitor:
    def test_register_unregister(self):
        monitor = HealthMonitor()
        monitor.register("m1")
        assert "m1" in monitor._members
        monitor.unregister("m1")
        assert "m1" not in monitor._members

    def test_record_heartbeat(self):
        monitor = HealthMonitor()
        monitor.register("m1")
        monitor.record_heartbeat("m1")
        mh = monitor._members["m1"]
        assert mh.state == HealthState.HEALTHY
        assert mh.consecutive_failures == 0

    def test_record_progress(self):
        monitor = HealthMonitor()
        monitor.register("m1")
        before = monitor._members["m1"].tasks_since_last_check
        monitor.record_progress("m1")
        assert monitor._members["m1"].tasks_since_last_check == before + 1

    def test_can_restart_within_limit(self):
        monitor = HealthMonitor(config=HealthConfig(max_restart_attempts=3))
        monitor.register("m1")
        assert monitor.can_restart("m1") is True

    def test_can_restart_exceeded(self):
        monitor = HealthMonitor(config=HealthConfig(max_restart_attempts=2))
        monitor.register("m1")
        monitor._members["m1"].restart_count = 2
        assert monitor.can_restart("m1") is False

    def test_record_restart(self):
        monitor = HealthMonitor()
        monitor.register("m1")
        monitor.record_restart("m1")
        assert monitor._members["m1"].restart_count == 1
        assert monitor._members["m1"].last_restart is not None

    def test_get_unhealthy(self):
        monitor = HealthMonitor()
        monitor.register("m1")
        monitor.register("m2")
        monitor._members["m1"].state = HealthState.STUCK
        monitor._members["m2"].state = HealthState.HEALTHY
        unhealthy = monitor.get_unhealthy()
        assert len(unhealthy) == 1
        assert unhealthy[0].member_id == "m1"

    async def test_check_once_healthy(self):
        monitor = HealthMonitor()
        monitor.register("m1")
        monitor.record_heartbeat("m1")
        await monitor.check_once()
        assert monitor._members["m1"].state == HealthState.HEALTHY

    async def test_check_once_stale(self):
        config = HealthConfig(stale_threshold=0.01, unresponsive_threshold=1000)
        monitor = HealthMonitor(config=config)
        monitor.register("m1")
        # Set heartbeat far in the past (time.time() based)
        monitor._members["m1"].last_heartbeat = time.time() - 5.0
        stale_called = []
        monitor.on_stale = lambda mid: stale_called.append(mid)
        await monitor.check_once()
        assert monitor._members["m1"].state == HealthState.STALE
        assert "m1" in stale_called

    async def test_check_once_stuck(self):
        config = HealthConfig(stuck_threshold=0.01)
        monitor = HealthMonitor(config=config)
        monitor.register("m1")
        monitor._members["m1"].last_progress = time.time() - 5.0
        monitor._members["m1"].last_heartbeat = time.time()  # Still alive
        stuck_called = []
        monitor.on_stuck = lambda mid: stuck_called.append(mid)
        await monitor.check_once()
        assert monitor._members["m1"].state == HealthState.STUCK
        assert "m1" in stuck_called

    async def test_mass_death_detection(self):
        config = HealthConfig(unresponsive_threshold=0.01)
        monitor = HealthMonitor(config=config)
        for i in range(4):
            mid = f"m{i}"
            monitor.register(mid)
            monitor._members[mid].last_heartbeat = time.time() - 500
            monitor._members[mid].consecutive_failures = 2  # Will become 3 → dead
        mass_death_ids = []
        monitor.on_mass_death = lambda ids: mass_death_ids.extend(ids)
        monitor.on_dead = lambda mid: None
        await monitor.check_once()
        # At least 3 dead members should trigger mass death
        assert len(mass_death_ids) >= 3

    async def test_start_stop(self):
        config = HealthConfig(check_interval=0.05)
        monitor = HealthMonitor(config=config)
        monitor.register("m1")
        monitor.record_heartbeat("m1")
        await monitor.start()
        await asyncio.sleep(0.1)  # Let a couple checks run
        await monitor.stop()
        assert monitor._task is None

    def test_backoff_delay(self):
        config = HealthConfig(backoff_base=2.0, max_restart_attempts=5)
        monitor = HealthMonitor(config=config)
        monitor.register("m1")
        monitor._members["m1"].restart_count = 3
        # No last_restart set → never restarted → backoff doesn't block
        assert monitor.can_restart("m1") is True
        # Now simulate a recent restart — backoff=2^3=8s should block
        monitor._members["m1"].last_restart = time.time()
        assert monitor.can_restart("m1") is False
