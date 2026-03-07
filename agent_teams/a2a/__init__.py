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

from .application import A2ATeamApp, create_a2a_team_app
from .channel import A2AChannel
from .storage import TeamTaskStorage
from .worker import TeamMemberWorker

__all__ = [
    "A2AChannel",
    "A2ATeamApp",
    "TeamMemberWorker",
    "TeamTaskStorage",
    "create_a2a_team_app",
]
