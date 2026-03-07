# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Artifact store for collecting and managing team outputs.

Artifacts are produced by agent members during task execution.
They can be intermediate results (passed between agents) or
final outputs aggregated for delivery.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional

from ..types import Artifact

logger = logging.getLogger(__name__)


class ArtifactStore(ABC):
    """Abstract base class for artifact storage."""

    @abstractmethod
    async def store(self, team_id: str, artifact: Artifact) -> Artifact:
        """Store an artifact and return it with any updated fields."""

    @abstractmethod
    async def get(self, team_id: str, artifact_id: str) -> Optional[Artifact]:
        """Retrieve an artifact by ID."""

    @abstractmethod
    async def list(self, team_id: str) -> list[Artifact]:
        """List all artifacts for a team."""

    @abstractmethod
    async def delete(self, team_id: str, artifact_id: str) -> bool:
        """Delete an artifact."""


class InMemoryArtifactStore(ArtifactStore):
    """Simple in-memory artifact store.

    Suitable for development and testing. For production, implement
    a persistent store (e.g., backed by object storage).
    """

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Artifact]] = {}
        self._lock = asyncio.Lock()

    async def store(self, team_id: str, artifact: Artifact) -> Artifact:
        async with self._lock:
            if team_id not in self._store:
                self._store[team_id] = {}
            self._store[team_id][artifact.id] = artifact
            logger.debug("Stored artifact %s for team %s", artifact.id, team_id)
            return artifact

    async def get(self, team_id: str, artifact_id: str) -> Optional[Artifact]:
        team_store = self._store.get(team_id, {})
        return team_store.get(artifact_id)

    async def list(self, team_id: str) -> list[Artifact]:
        team_store = self._store.get(team_id, {})
        return sorted(team_store.values(), key=lambda a: a.created_at)

    async def delete(self, team_id: str, artifact_id: str) -> bool:
        async with self._lock:
            team_store = self._store.get(team_id, {})
            return team_store.pop(artifact_id, None) is not None

    async def clear(self, team_id: str) -> None:
        """Remove all artifacts for a team."""
        async with self._lock:
            self._store.pop(team_id, None)
