# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""FastAPI application factory for agent-teams.

Usage
-----
Standalone (an app factory: nothing is built when this module is imported)::

    uvicorn --factory agent_teams.app:create_app --reload

or ``agent-teams serve``.

Mounted inside another application::

    from agent_teams.app import create_app
    parent_app.mount("/api/agent-teams/v1", create_app(manager=my_manager))
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from reactor import PluginManifest, hookimpl

from .manager import TeamManager
from .plugins.host import teams_platform
from .plugins.points import MEMBER_RUNNERS, MemberRunner
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


STUB_PLUGIN = "agent-teams.stub-model"


def _stub_factory(config: Any) -> Any:
    """A model member that runs nothing: what the app serves until a host contributes a runner."""

    async def _stub_run(prompt: str, context: dict | None = None) -> str:
        raise NotImplementedError(
            f"No agent runtime is contributed for member {config.name!r}: "
            "contribute a teams.member.runner for 'model' (teams_platform(model=…)) "
            "to the platform the TeamManager reads."
        )

    return _stub_run


class StubModelPlugin:
    """Contributes the stub as the ``model`` runner (MH-F07): a test plugin, by name."""

    @hookimpl
    def provide_contributions(self, contributions: Any) -> None:
        contributions.contribute(MEMBER_RUNNERS, MemberRunner("model", _stub_factory), contribution_id="model")


def stub_plugin() -> tuple[PluginManifest, StubModelPlugin]:
    """The stub model runner, as a plugin a platform registers beside the built-ins."""
    return (
        PluginManifest(
            name=STUB_PLUGIN,
            version="0.0.0",
            description="A model member that runs nothing, for a server nobody gave a runner.",
            tags=["agent-teams", "test"],
        ),
        StubModelPlugin(),
    )


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
        is made on a platform whose ``model`` runner is this module's stub
        (:func:`stub_plugin`): a model member's task raises
        ``NotImplementedError`` naming what to contribute — fine for
        testing, not for production.
    cors_origins:
        Allowed CORS origins.  Defaults to ``["*"]`` in development.
    """
    if manager is None:
        manager = TeamManager(platform=teams_platform(plugins=[stub_plugin()]))

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
