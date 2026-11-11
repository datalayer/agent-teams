# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""A seat on AWS AgentCore beside two local seats, against a fake relay.

The AgentCore member (``agentcore_deployment``) is asked over A2A through
Datalayer's relay: the deployment and the seat's session in the path, the
Datalayer key as the bearer, the session as the message's A2A context. The
relay answers as an A2A server does — a ``submitted`` task, then its end on
``tasks/get`` — and the runner follows it. The two other seats are local.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from agent_teams.manager import TeamManager
from agent_teams.plugins import runner_kind
from agent_teams.plugins import runners as runner_module
from agent_teams.types import ExecutionMode, TaskDefinition, TaskStatus

from .conftest import make_echo_factory, make_member, make_team_config, platform_with

pytest.importorskip("fasta2a")

DEPLOYMENT = "01K9AGENTCOREDEPLOYMENT0000"
BASE = "https://ai-agents.test"
KEY = "dl-key-for-the-test"


class FakeRelay:
    """ai-agents' A2A relay of an AgentCore deployment, as the runner reaches it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        call = json.loads(request.content)
        self.calls.append((str(request.url), request.headers.get("authorization", ""), call))
        prefix = f"{BASE}/api/ai-agents/v1/apps/deployments/{DEPLOYMENT}/agentcore/a2a/sessions/"
        assert str(request.url).startswith(prefix), request.url
        if call["method"] == "message/send":
            message = call["params"]["message"]
            task = {
                "id": "task-1",
                "contextId": message["contextId"],
                "kind": "task",
                "status": {"state": "submitted"},
                "history": [message],
            }
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": call["id"], "result": {"task": task}}
            )
        assert call["method"] == "tasks/get" and call["params"]["id"] == "task-1"
        done = {
            "id": "task-1",
            "contextId": "ctx",
            "kind": "task",
            "status": {"state": "completed"},
            "artifacts": [
                {
                    "artifactId": "a1",
                    "parts": [{"kind": "text", "text": "Found 3 sources on AgentCore."}],
                }
            ],
        }
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": call["id"], "result": done})


@pytest.fixture()
def relay(monkeypatch: pytest.MonkeyPatch) -> FakeRelay:
    fake = FakeRelay()
    monkeypatch.setattr(
        runner_module,
        "_http_client",
        lambda headers: httpx.AsyncClient(headers=headers, transport=httpx.MockTransport(fake)),
    )
    monkeypatch.setattr(runner_module, "FOLLOW_EVERY", 0.0)
    monkeypatch.setenv("DATALAYER_AI_AGENTS_URL", BASE)
    monkeypatch.setenv("DATALAYER_API_KEY", KEY)
    return fake


async def test_a_team_with_one_agentcore_seat_and_two_local_seats_runs_a_goal(
    relay: FakeRelay,
) -> None:
    planner = make_member("planner", "planner")
    researcher = make_member("researcher", "researcher", agentcore_deployment=DEPLOYMENT)
    writer = make_member("writer", "writer")
    assert runner_kind(researcher) == "a2a"
    manager = TeamManager(platform=platform_with(make_echo_factory()))
    team = await manager.create_team(
        make_team_config(
            execution_mode=ExecutionMode.PARALLEL, members=[planner, researcher, writer]
        )
    )
    await manager.start_team(team)
    done = await manager.execute_tasks(
        team,
        [
            # One task per seat: the parallel run hands each idle seat the next one.
            TaskDefinition(title="plan the brief"),
            TaskDefinition(title="research the sources"),
            TaskDefinition(title="write the brief"),
        ],
    )
    sessions = manager.sessions_of(team)
    assert sessions is not None
    seat = sessions.seat(researcher.id)
    await manager.stop_team(team)

    assert [task.status for task in done] == [TaskStatus.COMPLETED] * 3
    said = {task.member_id: str(task.output) for task in done}
    assert "Found 3 sources on AgentCore." in said[researcher.id]
    assert "[planner]" in said[planner.id] and "[writer]" in said[writer.id]

    # The relay was asked for the deployment, on the seat's session, with the key.
    assert seat is not None
    sent_url, bearer, sent = relay.calls[0]
    assert sent_url.endswith(f"/agentcore/a2a/sessions/{seat.uid}/")
    assert bearer == f"Bearer {KEY}"
    assert sent["params"]["message"]["contextId"] == seat.uid
    assert [call["method"] for _, _, call in relay.calls] == ["message/send", "tasks/get"]
    assert all(url == sent_url for url, _, _ in relay.calls)


async def test_an_agentcore_seat_needs_datalayer_and_names_only_its_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="takes no agent_endpoint"):
        make_member("researcher", agentcore_deployment=DEPLOYMENT, agent_endpoint="http://x/a2a")
    monkeypatch.delenv("DATALAYER_API_KEY", raising=False)
    monkeypatch.setenv("DATALAYER_AI_AGENTS_URL", BASE)
    run = runner_module.a2a_run_factory(make_member("researcher", agentcore_deployment=DEPLOYMENT))
    with pytest.raises(
        RuntimeError, match="DATALAYER_AI_AGENTS_URL and DATALAYER_API_KEY are both set"
    ):
        await run("research", {"session": {"uid": "01K9SEATSESSION000000000000"}})


async def test_a_remote_task_that_ends_failed_fails_the_seat(
    relay: FakeRelay, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        call = json.loads(request.content)
        if call["method"] == "message/send":
            task = {
                "id": "task-1",
                "contextId": "c",
                "kind": "task",
                "status": {"state": "working"},
            }
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": call["id"], "result": {"task": task}}
            )
        failed = {
            "id": "task-1",
            "contextId": "c",
            "kind": "task",
            "status": {"state": "failed"},
            "history": [
                {
                    "role": "agent",
                    "kind": "message",
                    "messageId": "m2",
                    "parts": [{"kind": "text", "text": "No model."}],
                }
            ],
        }
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": call["id"], "result": failed})

    monkeypatch.setattr(
        runner_module,
        "_http_client",
        lambda headers: httpx.AsyncClient(headers=headers, transport=httpx.MockTransport(failing)),
    )
    run = runner_module.a2a_run_factory(make_member("researcher", agentcore_deployment=DEPLOYMENT))
    with pytest.raises(RuntimeError, match=r"ended failed: No model\."):
        await run("research", {"session": {"uid": "01K9SEATSESSION000000000000"}})
