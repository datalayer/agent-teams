# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Team CRUD and lifecycle API routes.

These routes power the UI views:
- ``AgentTeamNew.tsx`` → POST /teams (create)
- ``AgentTeam.tsx`` → GET /teams/{id} (detail), POST /teams/{id}/start, etc.
- Team listing → GET /teams
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..manager import TeamManager
from ..types import (
    AssignTaskRequest,
    CreateTeamRequest,
    TaskDefinition,
    TeamConfig,
    TeamMetrics,
    TeamState,
    TeamStatus,
    TeamSummary,
    UpdateTeamRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/teams", tags=["teams"])


def _get_manager(request: Request) -> TeamManager:
    """Get the TeamManager from the FastAPI app state."""
    manager = getattr(request.app.state, "team_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=500,
            detail="TeamManager not initialized",
        )
    return manager


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("/", response_model=list[TeamSummary])
async def list_teams(request: Request) -> list[TeamSummary]:
    """List all teams."""
    manager = _get_manager(request)
    return manager.list_teams()


@router.post("/", response_model=dict[str, str], status_code=201)
async def create_team(request: Request, body: CreateTeamRequest) -> dict[str, str]:
    """Create a new team.

    Accepts the full ``TeamConfig`` and returns the team ID.
    """
    manager = _get_manager(request)
    try:
        team_id = await manager.create_team(body.config)
        return {"id": team_id}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/{team_id}", response_model=TeamState)
async def get_team(request: Request, team_id: str) -> TeamState:
    """Get the full state of a team."""
    manager = _get_manager(request)
    try:
        return manager.get_team(team_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Team {team_id} not found")


@router.patch("/{team_id}")
async def update_team(
    request: Request,
    team_id: str,
    body: UpdateTeamRequest,
) -> dict[str, str]:
    """Update team configuration (draft or stopped teams only)."""
    manager = _get_manager(request)
    try:
        team = manager.get_team(team_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Team {team_id} not found")

    if team.status not in (TeamStatus.DRAFT, TeamStatus.STOPPED):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot update team in {team.status.value} state",
        )

    # Apply updates
    if body.name is not None:
        team.config.name = body.name
    if body.description is not None:
        team.config.description = body.description
    if body.execution_mode is not None:
        team.config.execution_mode = body.execution_mode
    if body.routing_instructions is not None:
        team.config.routing_instructions = body.routing_instructions
    if body.validation is not None:
        team.config.validation = body.validation
    if body.notifications is not None:
        team.config.notifications = body.notifications
    if body.outputs is not None:
        team.config.outputs = body.outputs

    return {"status": "updated"}


@router.delete("/{team_id}", status_code=204)
async def delete_team(request: Request, team_id: str) -> None:
    """Delete a team."""
    manager = _get_manager(request)
    try:
        await manager.delete_team(team_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Team {team_id} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@router.post("/{team_id}/start")
async def start_team(request: Request, team_id: str) -> dict[str, str]:
    """Start a team."""
    manager = _get_manager(request)
    try:
        await manager.start_team(team_id)
        return {"status": "started"}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Team {team_id} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{team_id}/stop")
async def stop_team(request: Request, team_id: str) -> dict[str, str]:
    """Stop a running team."""
    manager = _get_manager(request)
    try:
        await manager.stop_team(team_id)
        return {"status": "stopped"}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Team {team_id} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{team_id}/pause")
async def pause_team(request: Request, team_id: str) -> dict[str, str]:
    """Pause a running team."""
    manager = _get_manager(request)
    try:
        await manager.pause_team(team_id)
        return {"status": "paused"}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Team {team_id} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{team_id}/resume")
async def resume_team(request: Request, team_id: str) -> dict[str, str]:
    """Resume a paused team."""
    manager = _get_manager(request)
    try:
        await manager.resume_team(team_id)
        return {"status": "resumed"}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Team {team_id} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@router.post("/{team_id}/tasks", response_model=TaskDefinition, status_code=201)
async def assign_task(
    request: Request,
    team_id: str,
    body: AssignTaskRequest,
) -> TaskDefinition:
    """Assign a task to the team."""
    manager = _get_manager(request)
    try:
        return await manager.assign_task(team_id, body)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Team {team_id} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/{team_id}/tasks", response_model=list[TaskDefinition])
async def list_tasks(request: Request, team_id: str) -> list[TaskDefinition]:
    """List all tasks for a team."""
    manager = _get_manager(request)
    try:
        team = manager.get_team(team_id)
        return team.tasks
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Team {team_id} not found")


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@router.get("/{team_id}/metrics", response_model=TeamMetrics)
async def get_metrics(request: Request, team_id: str) -> TeamMetrics:
    """Get team metrics."""
    manager = _get_manager(request)
    try:
        return manager.get_metrics(team_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Team {team_id} not found")
