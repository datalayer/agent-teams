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
    BUDGET_FIELD,
    CHECKPOINT_FIELD,
    CREDENTIAL_FIELD,
    ENVELOPE_KEY,
    ERROR_FIELD,
    EXECUTION_FIELD,
    ORCHESTRATION_EXTENSION_URI,
    PAUSE_FIELD,
    PAUSED_FIELD,
    STEER_METHOD,
    USAGE_FIELD,
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

# `orchestration_extension` above has no framework dependency at all — the
# whole point of O3-02 — but everything below this line is built on
# `fasta2a`, which is this package's optional `a2a` extra, not a core
# dependency: `agent_runtimes` (and anyone else) depends on `agent-teams`
# unconditionally but only pulls `fasta2a` in through its own `a2a` extra,
# so `import agent_teams.a2a.orchestration_extension` — which Python cannot
# do without first running this file — must not fail for an install that
# has the former and not the latter.
try:
    from .reference_worker import ReferenceWorker, create_reference_app

    _REFERENCE_WORKER_EXPORTS = ["ReferenceWorker", "create_reference_app"]
except ImportError:
    _REFERENCE_WORKER_EXPORTS = []

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
    "ENVELOPE_KEY",
    "STEER_METHOD",
    "EXECUTION_FIELD",
    "BUDGET_FIELD",
    "CREDENTIAL_FIELD",
    "CHECKPOINT_FIELD",
    "PAUSE_FIELD",
    "USAGE_FIELD",
    "PAUSED_FIELD",
    "ERROR_FIELD",
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
    *_REFERENCE_WORKER_EXPORTS,
    *_LEGACY_EXPORTS,
]
