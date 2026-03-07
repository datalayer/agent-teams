# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""FastAPI application factory for agent-teams.

Usage
-----
Standalone::

    uvicorn agent_teams.app:app --reload

Mounted inside another application::

    from agent_teams.app import create_app
    parent_app.mount("/api/agent-teams/v1", create_app(manager=my_manager))
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .manager import TeamManager
from .routes.events import router as events_router
from .routes.teams import router as teams_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle."""
    manager: TeamManager = app.state.team_manager
    logger.info("agent-teams: starting up (teams=%d)", len(manager._teams))
    yield
    # Graceful shutdown: stop all running teams
    for team_id, state in list(manager._teams.items()):
        from .types import TeamStatus

        if state.status in (TeamStatus.RUNNING, TeamStatus.PAUSED):
            logger.info("agent-teams: stopping team %s", team_id)
            try:
                await manager.stop_team(team_id)
            except Exception:
                logger.exception("Error stopping team %s during shutdown", team_id)
    logger.info("agent-teams: shut down")


def create_app(
    *,
    manager: TeamManager | None = None,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """Create a FastAPI application for the agent-teams API.

    Parameters
    ----------
    manager:
        An existing :class:`TeamManager` to use.  When *None* a new one
        is created with a default ``member_run_factory`` that raises
        ``NotImplementedError`` (fine for testing, not for production).
    cors_origins:
        Allowed CORS origins.  Defaults to ``["*"]`` in development.
    """
    if manager is None:

        def _stub_factory(config):
            async def _stub_run(prompt: str, context: dict | None = None) -> str:
                raise NotImplementedError(
                    f"No agent runtime configured for member {config.name!r}. "
                    "Provide a real member_run_factory when creating the TeamManager."
                )

            return _stub_run

        manager = TeamManager(member_run_factory=_stub_factory)

    app = FastAPI(
        title="Datalayer Agent Teams",
        description="Manage teams of AI agents",
        version="0.1.0",
        lifespan=_lifespan,
    )

    # Store manager in app state
    app.state.team_manager = manager

    # CORS
    origins = cors_origins or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount routers
    app.include_router(teams_router)
    app.include_router(events_router)

    @app.get("/health")
    async def health():
        return {"status": "ok", "teams": len(manager._teams)}

    return app


# Convenience: ``uvicorn agent_teams.app:app``
app = create_app()
