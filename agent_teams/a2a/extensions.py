# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""A2A Team Coordination Extension.

Implements the Datalayer team coordination protocol as an A2A extension,
following the ``AgentExtension`` specification from A2A v0.3.0.

Extension URI: ``https://datalayer.io/ext/team-coordination/v1``

This extension adds team-specific metadata to A2A messages and tasks:

* **Member assignment**: which team member owns a task
* **Task dependencies**: DAG relationships between tasks
* **Health status**: heartbeat + liveness information
* **Reaction triggers**: automated responses to lifecycle events
* **Orchestration hints**: execution mode, priority, role matching

The extension is *data-only* — it enriches the ``metadata`` maps on
messages, tasks, and artifacts without introducing new RPC methods
or changing the A2A state machine.
"""

from __future__ import annotations

from typing import Any

# Extension URI — stable, versioned identifier
TEAM_COORDINATION_URI = "https://datalayer.io/ext/team-coordination/v1"

# Metadata key prefix for all team coordination data
_PREFIX = "x-datalayer-team"


def agent_extension(
    *,
    required: bool = False,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the ``AgentExtension`` dict for the team coordination extension.

    This is meant to be passed into ``FastA2A(extensions=[...])`` or
    registered in an ``AgentCard``'s ``capabilities.extensions``.

    Args:
        required: Whether clients *must* activate this extension.
        params: Optional configuration parameters.
    """
    ext: dict[str, Any] = {
        "uri": TEAM_COORDINATION_URI,
        "description": (
            "Datalayer team coordination: member assignment, task dependencies, "
            "health monitoring, and reaction triggers for multi-agent teams."
        ),
        "required": required,
    }
    if params:
        ext["params"] = params
    return ext


# ------------------------------------------------------------------
# Metadata helpers — produce / consume metadata dicts
# ------------------------------------------------------------------

def make_task_metadata(
    *,
    team_id: str,
    assigned_to: str | None = None,
    priority: int | None = None,
    depends_on: list[str] | None = None,
    execution_mode: str | None = None,
    role_hint: str | None = None,
) -> dict[str, Any]:
    """Build ``metadata`` for an A2A task carrying team coordination info."""
    md: dict[str, Any] = {f"{_PREFIX}.team-id": team_id}
    if assigned_to:
        md[f"{_PREFIX}.assigned-to"] = assigned_to
    if priority is not None:
        md[f"{_PREFIX}.priority"] = priority
    if depends_on:
        md[f"{_PREFIX}.depends-on"] = depends_on
    if execution_mode:
        md[f"{_PREFIX}.execution-mode"] = execution_mode
    if role_hint:
        md[f"{_PREFIX}.role-hint"] = role_hint
    return md


def make_health_metadata(
    *,
    member_id: str,
    state: str,
    last_heartbeat: str | None = None,
    consecutive_failures: int = 0,
) -> dict[str, Any]:
    """Build ``metadata`` for a health status update."""
    md: dict[str, Any] = {
        f"{_PREFIX}.health.member-id": member_id,
        f"{_PREFIX}.health.state": state,
        f"{_PREFIX}.health.failures": consecutive_failures,
    }
    if last_heartbeat:
        md[f"{_PREFIX}.health.last-heartbeat"] = last_heartbeat
    return md


def make_reaction_metadata(
    *,
    trigger: str,
    action: str,
    target_id: str,
    attempt: int = 1,
    escalated: bool = False,
) -> dict[str, Any]:
    """Build ``metadata`` for a reaction event."""
    return {
        f"{_PREFIX}.reaction.trigger": trigger,
        f"{_PREFIX}.reaction.action": action,
        f"{_PREFIX}.reaction.target-id": target_id,
        f"{_PREFIX}.reaction.attempt": attempt,
        f"{_PREFIX}.reaction.escalated": escalated,
    }


def extract_team_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Extract only team coordination keys from a metadata dict."""
    return {k: v for k, v in metadata.items() if k.startswith(_PREFIX)}


def is_team_extension_active(activated_extensions: list[str]) -> bool:
    """Check whether the team coordination extension was activated."""
    return TEAM_COORDINATION_URI in activated_extensions
