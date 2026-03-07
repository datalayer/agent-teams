# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Tests for the FastAPI routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_teams.app import create_app
from agent_teams.manager import TeamManager
from agent_teams.types import AgentMemberConfig, ExecutionMode, TeamConfig

from .conftest import make_echo_factory, make_team_config


@pytest.fixture
def client() -> TestClient:
    manager = TeamManager(member_run_factory=make_echo_factory())
    app = create_app(manager=manager)
    return TestClient(app)


@pytest.fixture
def config() -> TeamConfig:
    return make_team_config()


class TestHealthEndpoint:

    def test_health(self, client: TestClient):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestTeamsCRUD:

    def test_list_teams_empty(self, client: TestClient):
        resp = client.get("/teams/")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_team(self, client: TestClient, config: TeamConfig):
        resp = client.post(
            "/teams/",
            json={"config": config.model_dump(mode="json")},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data

    def test_create_duplicate(self, client: TestClient, config: TeamConfig):
        payload = {"config": config.model_dump(mode="json")}
        client.post("/teams/", json=payload)
        resp = client.post("/teams/", json=payload)
        assert resp.status_code == 409

    def test_get_team(self, client: TestClient, config: TeamConfig):
        create_resp = client.post(
            "/teams/",
            json={"config": config.model_dump(mode="json")},
        )
        team_id = create_resp.json()["id"]
        resp = client.get(f"/teams/{team_id}")
        assert resp.status_code == 200
        assert resp.json()["config"]["name"] == config.name

    def test_get_nonexistent(self, client: TestClient):
        resp = client.get("/teams/nope")
        assert resp.status_code == 404

    def test_delete_team(self, client: TestClient, config: TeamConfig):
        create_resp = client.post(
            "/teams/",
            json={"config": config.model_dump(mode="json")},
        )
        team_id = create_resp.json()["id"]
        resp = client.delete(f"/teams/{team_id}")
        assert resp.status_code == 204

    def test_delete_nonexistent(self, client: TestClient):
        resp = client.delete("/teams/nope")
        assert resp.status_code == 404


class TestTeamLifecycleRoutes:

    def _create_team(self, client: TestClient, config: TeamConfig) -> str:
        resp = client.post(
            "/teams/",
            json={"config": config.model_dump(mode="json")},
        )
        return resp.json()["id"]

    def test_start(self, client: TestClient, config: TeamConfig):
        team_id = self._create_team(client, config)
        resp = client.post(f"/teams/{team_id}/start")
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"

    def test_stop(self, client: TestClient, config: TeamConfig):
        team_id = self._create_team(client, config)
        client.post(f"/teams/{team_id}/start")
        resp = client.post(f"/teams/{team_id}/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"

    def test_pause_resume(self, client: TestClient, config: TeamConfig):
        team_id = self._create_team(client, config)
        client.post(f"/teams/{team_id}/start")

        resp = client.post(f"/teams/{team_id}/pause")
        assert resp.status_code == 200

        resp = client.post(f"/teams/{team_id}/resume")
        assert resp.status_code == 200

    def test_start_nonexistent(self, client: TestClient):
        resp = client.post("/teams/nope/start")
        assert resp.status_code == 404


class TestTaskRoutes:

    def _setup_running_team(self, client: TestClient, config: TeamConfig) -> str:
        resp = client.post(
            "/teams/",
            json={"config": config.model_dump(mode="json")},
        )
        team_id = resp.json()["id"]
        client.post(f"/teams/{team_id}/start")
        return team_id

    def test_assign_task(self, client: TestClient, config: TeamConfig):
        team_id = self._setup_running_team(client, config)
        resp = client.post(
            f"/teams/{team_id}/tasks",
            json={"title": "Analyze data", "description": "Run analysis"},
        )
        assert resp.status_code == 201
        assert resp.json()["title"] == "Analyze data"

    def test_list_tasks(self, client: TestClient, config: TeamConfig):
        team_id = self._setup_running_team(client, config)
        client.post(
            f"/teams/{team_id}/tasks",
            json={"title": "T1", "description": "D1"},
        )
        resp = client.get(f"/teams/{team_id}/tasks")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1


class TestMetricsRoute:

    def test_get_metrics(self, client: TestClient, config: TeamConfig):
        resp = client.post(
            "/teams/",
            json={"config": config.model_dump(mode="json")},
        )
        team_id = resp.json()["id"]
        resp = client.get(f"/teams/{team_id}/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks_completed" in data
        assert "tasks_pending" in data


class TestEventsRoutes:

    def _setup_team(self, client: TestClient, config: TeamConfig) -> str:
        resp = client.post(
            "/teams/",
            json={"config": config.model_dump(mode="json")},
        )
        team_id = resp.json()["id"]
        client.post(f"/teams/{team_id}/start")
        return team_id

    def test_events_history(self, client: TestClient, config: TeamConfig):
        team_id = self._setup_team(client, config)
        resp = client.get(f"/teams/{team_id}/events/history")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
