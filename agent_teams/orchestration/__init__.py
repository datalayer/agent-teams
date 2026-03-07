# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Orchestration strategies for Agent Teams.

Each strategy implements a different pattern for coordinating
work across team members. Strategies are pluggable — the
TeamManager selects one based on ``TeamConfig.execution_mode``.
"""

from .base import BaseOrchestrator, MemberProxy, OrchestratorContext
from .parallel import ParallelOrchestrator
from .sequential import SequentialOrchestrator
from .supervisor import SupervisorOrchestrator

__all__ = [
    "BaseOrchestrator",
    "MemberProxy",
    "OrchestratorContext",
    "ParallelOrchestrator",
    "SequentialOrchestrator",
    "SupervisorOrchestrator",
]
