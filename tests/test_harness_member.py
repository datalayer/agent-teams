# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""A team whose member is a coding agent, run behind the Shield (SHIELD SH-45).

Beside a model member (the echo of the tests), the reviewer is a
harness — an agent of the Agent Client Protocol behind ``agent-shield run``
over stdio, in its own folder of the team's workspace. Its file write goes
through the Shield's policy; a ``git push`` the Shield would ask a person
about is refused, nobody watching a team run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent_teams.manager import TeamManager
from agent_teams.types import AgentMemberConfig, ExecutionMode, TaskDefinition, TaskStatus

from .conftest import make_echo_factory, make_member, make_team_config, platform_with

pytest.importorskip(
    "agent_shield", reason="a harness member runs behind the Shield: agent-teams[shield]"
)

from agent_teams.harness import HarnessRefusedError, harness_run_factory

AGENT = [sys.executable, str(Path(__file__).parent / "fixtures" / "coding_agent.py")]


def _policy(tmp_path: Path) -> Path:
    from agent_shield.cli import app
    from typer.testing import CliRunner

    path = tmp_path / "agent-shield.yaml"
    result = CliRunner().invoke(app, ["policy", "init", str(path)])
    assert result.exit_code == 0, result.output
    return path


def test_a_harness_member_brings_its_own_tools_and_prompt():
    with pytest.raises(ValueError, match="it takes no mcp_server, tools"):
        AgentMemberConfig(
            name="reviewer", harness="claude-code", mcp_server="datalayer", tools=["read"]
        )
    with pytest.raises(ValueError, match="it takes no agent_endpoint"):
        AgentMemberConfig(name="reviewer", harness="codex", agent_endpoint="http://elsewhere/a2a")
    assert AgentMemberConfig(name="reviewer", harness="codex").harness == "codex"


async def test_a_team_with_a_harness_member_needs_the_harness_runner():
    manager = TeamManager(platform=platform_with(make_echo_factory()))
    team = await manager.create_team(
        make_team_config(
            execution_mode=ExecutionMode.SEQUENTIAL,
            members=[make_member("reviewer", harness="codex")],
        )
    )
    with pytest.raises(ValueError, match="No teams.member.runner is contributed for a harness member"):
        await manager.start_team(team)
    assert manager.get_team(team).status.value == "draft"


async def test_a_team_runs_a_coder_and_a_coding_agent_behind_the_shield(tmp_path: Path):
    workspace = tmp_path / "work"
    manager = TeamManager(
        platform=platform_with(
            make_echo_factory(),
            harness=harness_run_factory(
                workspace,
                policy=_policy(tmp_path),
                commands={"coding-agent": AGENT},
            ),
        )
    )
    reviewer = make_member("reviewer", "reviewer", harness="coding-agent")
    coder = make_member("coder", "coder")
    team = await manager.create_team(
        make_team_config(execution_mode=ExecutionMode.SEQUENTIAL, members=[reviewer, coder])
    )
    await manager.start_team(team)
    # The sequential runner hands a task to the first idle member: the reviewer.
    (review,) = await manager.execute_tasks(
        team, [TaskDefinition(title="review the greeting, then push")]
    )
    assert review.member_id == reviewer.id
    assert review.status == TaskStatus.COMPLETED, review.error
    output = str(review.output)
    assert "noted:" in output and "review the greeting" in output
    # The push the Shield asked about was refused, nobody being there to answer.
    assert (
        "push: reject" in output
        and "Refused, nobody being there to ask: git push origin main" in output
    )
    # Its edit went through the Shield, in the member's own folder.
    notes = workspace / reviewer.id / "NOTES.md"
    assert notes.is_file() and "review the greeting" in notes.read_text()


async def test_an_unknown_harness_is_refused_in_the_register_s_sentence(tmp_path: Path):
    pytest.importorskip("agentspecs", reason="the catalogue is agentspecs'")
    factory = harness_run_factory(tmp_path)
    with pytest.raises(HarnessRefusedError, match="not in the register"):
        factory.factory(make_member("reviewer", harness="no-such-agent"))


async def test_a_harness_member_keeps_one_session_for_the_run(tmp_path: Path):
    """MH-23: two tasks of one member share a session; the run's end ends it; `local status` shows it."""
    from agent_teams.sessions import FileSessionStore

    workspace = tmp_path / "work"
    runner = harness_run_factory(workspace, policy=_policy(tmp_path), commands={"coding-agent": AGENT})
    manager = TeamManager(platform=platform_with(make_echo_factory(), harness=runner))
    reviewer = make_member("reviewer", "reviewer", harness="coding-agent")
    team = await manager.create_team(
        make_team_config(execution_mode=ExecutionMode.SEQUENTIAL, members=[reviewer])
    )
    await manager.start_team(team)
    first, second = await manager.execute_tasks(
        team, [TaskDefinition(title="note the first"), TaskDefinition(title="note the second")]
    )
    assert first.status == second.status == TaskStatus.COMPLETED, (first.error, second.error)
    sessions = manager.sessions_of(team)
    assert sessions is not None
    seat = sessions.seat(reviewer.id)
    assert seat is not None and seat.turns == 2
    # One ACP session served both tasks, kept beside the seat's session as its harness row.
    store = FileSessionStore()
    row = store.harness_session(seat.uid)
    assert row is not None and row["acp_session_id"] == runner.session_of(reviewer.id) == "s1"
    assert row["harness"] == "coding-agent" and row["seat"] == reviewer.id
    described = store.describe(seat.uid)
    assert described["turns"] == 2 and described["harness"] == "coding-agent"
    assert described["runs_on"] == {"kind": "team_run", "execution_id": sessions.execution_id, "seat": reviewer.id}
    # What `agent-teams local status <team>` reads (the team's state over the
    # route, `test_routes.py`): the run as a session with its seats.
    shown = manager.get_team(team).model_dump(mode="json")
    assert shown["session"]["state"] == "open"
    assert shown["session"]["seats"] == [
        {"id": reviewer.id, "session_uid": seat.uid, "agent": "harness:coding-agent", "harness": "coding-agent"}
    ]
    await manager.stop_team(team)
    assert runner.session_of(reviewer.id) is None
    assert store.describe(seat.uid)["state"] == "ended"
    assert manager.get_team(team).session["state"] == "ended"
