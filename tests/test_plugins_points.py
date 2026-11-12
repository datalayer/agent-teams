# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""A team run's contribution points (MH-F07): the runners and the orchestrations.

A seat's runner is chosen by the member's kind through `teams.member.runner`
and a run's orchestration by its mode through `teams.session.orchestration`;
a mode nobody contributes (`graph`) is refused in a sentence rather than
served by the supervisor's; a malformed or duplicate contribution is refused
naming the plugin, the host running with the rest; the orchestration owns
the seats' sessions — one per seat for the run — and `post-team-stop` ends
them.
"""

from __future__ import annotations

from typing import Any

import pytest
from reactor import PluginManifest, hookimpl

from agent_teams.hooks import HookEvent, HookResult
from agent_teams.manager import TeamManager
from agent_teams.plugins import (
    MEMBER_RUNNERS,
    ORCHESTRATIONS,
    MemberRunner,
    Orchestration,
    accepted,
    orchestration_for,
    refusals,
    runner_for,
    runner_kind,
)
from agent_teams.sessions import FileSessionStore
from agent_teams.types import ExecutionMode, TaskDefinition, TaskStatus

from .conftest import make_echo_factory, make_member, make_team_config, platform_with


class _Contributes:
    def __init__(self, *offers: tuple[Any, Any, str]) -> None:
        self._offers = offers

    @hookimpl
    def provide_contributions(self, contributions: Any) -> None:
        for point, value, contribution_id in self._offers:
            contributions.contribute(point, value, contribution_id=contribution_id)


def _plugin(name: str, *offers: tuple[Any, Any, str]) -> tuple[PluginManifest, Any]:
    return PluginManifest(name=name, version="0.0.0", description=name), _Contributes(*offers)


def test_the_kind_of_runner_a_member_takes() -> None:
    assert runner_kind(make_member("coder")) == "model"
    assert runner_kind(make_member("reviewer", harness="codex")) == "harness"
    remote = make_member("remote", agent_endpoint="http://elsewhere/a2a")
    assert runner_kind(remote, a2a=True) == "a2a"
    assert runner_kind(remote, a2a=False) == "model"


def test_the_built_ins_and_the_hosts_model_runner_are_contributed() -> None:
    platform = platform_with(make_echo_factory())
    assert [one.id for one in accepted(platform, MEMBER_RUNNERS)] == ["a2a", "model"]
    assert [one.id for one in accepted(platform, ORCHESTRATIONS)] == [
        "sequential",
        "parallel",
        "supervisor",
    ]
    assert runner_for(platform, "model").kind == "model"
    assert refusals(platform) == []


def test_a_missing_runner_is_refused_naming_what_to_contribute() -> None:
    platform = platform_with()
    with pytest.raises(
        ValueError,
        match=r"No teams\.member\.runner is contributed for a model member: a host contributes",
    ):
        runner_for(platform, "model")
    with pytest.raises(
        ValueError, match="for a harness member: a coding agent runs behind the Shield"
    ):
        runner_for(platform, "harness")


def test_graph_is_refused_not_served_by_the_supervisor() -> None:
    platform = platform_with(make_echo_factory())
    with pytest.raises(
        ValueError, match=r"execution mode 'graph': one of sequential, parallel, supervisor"
    ):
        orchestration_for(platform, ExecutionMode.GRAPH)
    with pytest.raises(ValueError, match=r"not served by 'supervisor' in its place"):
        orchestration_for(platform, "graph")
    assert orchestration_for(platform, ExecutionMode.PARALLEL).mode == "parallel"


def test_a_malformed_or_duplicate_contribution_is_refused_naming_the_plugin() -> None:
    platform = platform_with(
        make_echo_factory(),
        plugins=[
            _plugin(
                "rival",
                (MEMBER_RUNNERS, MemberRunner("model", make_echo_factory()), "model"),
                (MEMBER_RUNNERS, "not a runner", "harness"),
                (ORCHESTRATIONS, Orchestration("graph", object, object), "graph"),
            )
        ],
    )
    lines = [one.line() for one in refusals(platform)]
    assert any(
        "rival: teams.member.runner 'model' refused" in line
        and "agent-teams.runs contributed first" in line
        for line in lines
    )
    assert any(
        "rival: teams.member.runner 'harness' refused" in line and "not a MemberRunner" in line
        for line in lines
    )
    assert any(
        "rival: teams.session.orchestration 'graph' refused" in line
        and "'graph' that is none" in line
        for line in lines
    )
    # The host runs with the rest: the built-in model runner stands.
    assert runner_for(platform, "model").factory is not None
    assert [one.plugin for one in accepted(platform, MEMBER_RUNNERS) if one.id == "model"] == [
        "agent-teams.runs"
    ]


async def test_a_test_runner_contributed_is_used_for_a_seat() -> None:
    def shouting(member: Any) -> Any:
        async def run(prompt: str, context: dict[str, Any]) -> str:
            return f"[{member.name} SHOUTS] {prompt.upper()}"

        return run

    platform = platform_with(
        plugins=[_plugin("loud", (MEMBER_RUNNERS, MemberRunner("model", shouting), "model"))]
    )
    manager = TeamManager(platform=platform)
    team = await manager.create_team(
        make_team_config(execution_mode=ExecutionMode.SEQUENTIAL, members=[make_member("coder")])
    )
    await manager.start_team(team)
    (done,) = await manager.execute_tasks(team, [TaskDefinition(title="write it")])
    assert done.status == TaskStatus.COMPLETED and "[coder SHOUTS] # TASK: WRITE IT" in str(
        done.output
    )
    await manager.stop_team(team)


async def test_a_team_with_a_graph_mode_is_refused_at_start() -> None:
    manager = TeamManager(platform=platform_with(make_echo_factory()))
    team = await manager.create_team(
        make_team_config(execution_mode=ExecutionMode.GRAPH, members=[make_member("coder")])
    )
    with pytest.raises(ValueError, match="execution mode 'graph'"):
        await manager.start_team(team)
    assert manager.get_team(team).status.value == "draft"


async def test_the_orchestration_owns_one_session_per_seat_and_the_stop_ends_them(
    tmp_path: Any,
) -> None:
    manager = TeamManager(platform=platform_with(make_echo_factory()))
    coder, reviewer = make_member("coder"), make_member("reviewer", "reviewer")
    team = await manager.create_team(
        make_team_config(execution_mode=ExecutionMode.SEQUENTIAL, members=[coder, reviewer])
    )
    opened: list[tuple[str, str]] = []
    ended: list[dict[str, str]] = []

    @manager.hooks.on(HookEvent.SESSION_OPEN)
    async def on_open(event: Any, seat: str, session_uid: str, **facts: Any) -> HookResult:
        opened.append((seat, session_uid))
        return HookResult(allow=True)

    @manager.hooks.on(HookEvent.SESSION_END)
    async def on_end(event: Any, seats: dict[str, str], **facts: Any) -> HookResult:
        ended.append(dict(seats))
        return HookResult(allow=True)

    await manager.start_team(team)
    state = manager.get_team(team)
    assert state.session["state"] == "open" and state.session["seats"] == []
    await manager.execute_tasks(team, [TaskDefinition(title="one"), TaskDefinition(title="two")])
    sessions = manager.sessions_of(team)
    assert sessions is not None
    # Two tasks of the sequential run went to the first idle member: one session, two turns.
    seat = sessions.seat(coder.id)
    assert seat is not None and seat.turns == 2
    assert [s for s, _ in opened] == [coder.id]
    store = FileSessionStore()
    described = store.describe(seat.uid)
    assert described is not None
    assert described["turns"] == 2 and described["state"] == "open"
    assert described["runs_on"] == {
        "kind": "team_run",
        "execution_id": sessions.execution_id,
        "seat": coder.id,
    }
    assert described["agent"] == "model:anthropic-claude-sonnet-4"
    await manager.stop_team(team)
    assert ended == [{coder.id: seat.uid}]
    assert store.describe(seat.uid)["state"] == "ended"
    assert manager.get_team(team).session["state"] == "ended"
    assert manager.sessions_of(team) is None


async def test_a_hook_that_disallows_refuses_the_seats_session() -> None:
    manager = TeamManager(platform=platform_with(make_echo_factory()))
    team = await manager.create_team(
        make_team_config(execution_mode=ExecutionMode.SEQUENTIAL, members=[make_member("coder")])
    )

    @manager.hooks.on(HookEvent.SESSION_OPEN)
    async def refuse(event: Any, **facts: Any) -> HookResult:
        return HookResult(allow=False, reason="Nobody opens a seat today.")

    await manager.start_team(team)
    (done,) = await manager.execute_tasks(team, [TaskDefinition(title="one")])
    # The refusal, in the hook's sentence, is the task's failure; no session was opened.
    assert done.status == TaskStatus.FAILED and "Nobody opens a seat today." in str(done.error)
    assert manager.sessions_of(team).seats() == []
    await manager.stop_team(team)
