# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""A2A (Agent-to-Agent) protocol integration via fasta2a.

This package bridges agent-teams with the A2A protocol using fasta2a,
enabling:
- Each team member to be exposed as an A2A agent endpoint.
- The team itself to be exposed as a composite A2A agent.
- A2A-based communication between remote agent members.
- SSE streaming of task progress via A2A's message/stream protocol.

Architecture::

    ┌───────────────────────────────────────┐
    │            TeamManager                │
    │  ┌─────────┐  ┌────────────────────┐  │
    │  │Orch.    │  │  A2ATeamApp        │  │
    │  │Strategy │  │  (FastA2A endpoint)│  │
    │  └────┬────┘  └────────┬───────────┘  │
    │       │                │              │
    │  ┌────▼────────────────▼───────────┐  │
    │  │   A2AChannel (remote members)   │  │
    │  │   InMemoryChannel (local)       │  │
    │  └────┬────────────────────────────┘  │
    │       │                               │
    │  ┌────▼─────────┐  ┌───────────────┐  │
    │  │TeamWorker    │  │TeamStorage    │  │
    │  │(fasta2a      │  │(wraps fasta2a │  │
    │  │ Worker)      │  │ Storage)      │  │
    │  └──────────────┘  └───────────────┘  │
    └───────────────────────────────────────┘
"""

from .extensions import (
    TEAM_COORDINATION_URI,
    agent_extension,
    extract_team_metadata,
    is_team_extension_active,
    make_health_metadata,
    make_reaction_metadata,
    make_task_metadata,
)
from .orchestration_extension import (
    ORCHESTRATION_EXTENSION_URI,
    STEER_METHOD,
    Budget,
    ExecutionRef,
    Usage,
    build_delegation_meta,
    error_meta,
    extension_agent_extension,
    is_extension_active,
    paused_meta,
    read_delegation_meta,
    read_usage_meta,
    steer_notification,
    usage_meta,
)

# `application`, `channel`, `storage` and `worker` build A2A `Message`/`Part`
# payloads against an older fasta2a shape — `TextPart(kind="text", ...)`,
# `part.get("kind")` — that the installed `fasta2a` no longer has: `Part` is
# now a flat `{text, raw, url, data}` dict with no `kind` discriminator at
# all, on top of the `StreamingStorageWrapper` and `A2AClient(base_url=...)`
# drift already found and fixed here. Wrapped the same way the top-level
# `agent_teams/__init__.py` already wraps this whole subpackage, so that one
# genuinely broken corner does not take the orchestration extension above
# down with it. Fixing the message-shape drift itself is a separate,
# larger pass across four files this change does not attempt.
try:
    from .application import A2ATeamApp, create_a2a_team_app
    from .channel import A2AChannel, CompositeChannel
    from .storage import TeamTaskStorage
    from .worker import TeamMemberWorker

    _LEGACY_EXPORTS = [
        "A2AChannel",
        "A2ATeamApp",
        "CompositeChannel",
        "TeamMemberWorker",
        "TeamTaskStorage",
        "create_a2a_team_app",
    ]
except ImportError:
    _LEGACY_EXPORTS = []

__all__ = [
    "TEAM_COORDINATION_URI",
    "agent_extension",
    "extract_team_metadata",
    "is_team_extension_active",
    "make_health_metadata",
    "make_reaction_metadata",
    "make_task_metadata",
    # The orchestration extension (ORCHESTRATOR.md O3-01, O3-02).
    "ORCHESTRATION_EXTENSION_URI",
    "STEER_METHOD",
    "Budget",
    "ExecutionRef",
    "Usage",
    "build_delegation_meta",
    "error_meta",
    "extension_agent_extension",
    "is_extension_active",
    "paused_meta",
    "read_delegation_meta",
    "read_usage_meta",
    "steer_notification",
    "usage_meta",
    *_LEGACY_EXPORTS,
]
