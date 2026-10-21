# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""`agent-teams sessions …`: a team run as a session (MH-22, MH-41, MH-24, MH-25, MH-39, MH-40).

Against a fake control plane and record: a run of the `coding-agents` team
with its two seats, each seat's session read from the record by the uid its
execution's attempt carries; the run's state mapped from the execution's,
any other word refused; one test per verb and one per refusal sentence;
`sessions list --team coding-agents` shows the run.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from datalayer_core.cli.commands.orchestration_common import OrchestrationCommandError
from datalayer_core.orchestration import (
    AgentBinding,
    AgentProtocol,
    Attempt,
    Execution,
    ExecutionState,
    Objective,
)
from typer.testing import CliRunner

pytest.importorskip("datalayer_core", reason="the platform commands need agent-teams[datalayer]")
pytest.importorskip("agent_runtimes.specs.teams", reason="the team catalogue ships with agent-runtimes")

from agent_teams import platform_cli, sessions_commands
from agent_teams.cli import app
from agent_teams.sessions import FileSessionStore, TeamSessionStore
from agent_teams.sessions_commands import (
    EXECUTION_TO_SESSION,
    session_state_of,
)

runner = CliRunner(env={"NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "220", "_TYPER_STANDARD_TRACEBACK": "1"})
NOW = "2026-10-21T10:07:03Z"
TEAM = "coding-agents"
ROOT = "exec_team"


def plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def an_execution(execution_id: str, agent_id: str, *, parent: str | None = None, root: str = ROOT, depth: int = 0, state: ExecutionState = ExecutionState.COMPLETED, created: str = NOW) -> Execution:
    return Execution(
        execution_id=execution_id,
        parent_execution_id=parent,
        root_execution_id=root,
        depth=depth,
        status=state,
        agent=AgentBinding(agent_id=agent_id, capability="c", protocol=AgentProtocol.A2A),
        objective=Objective(goal="Add a test, then review it"),
        created_at=created,
        updated_at=created,
    )


def an_attempt(execution_id: str, session_id: str | None) -> Attempt:
    return Attempt(
        attempt_id=f"att-{execution_id}",
        execution_id=execution_id,
        number=1,
        state=ExecutionState.COMPLETED,
        agent_id=f"team:{TEAM}/{execution_id.rpartition('_')[2]}",
        protocol=AgentProtocol.A2A,
        session_id=session_id,
        protocol_task_id="task-1",
        started_at=NOW,
    )


def _session_row(uid: str, *, agent: str, harness: str, turns: int, state: str = "paused") -> dict[str, Any]:
    return {
        "uid": uid, "app_uid": f"team:{TEAM}", "owner_uid": "ada", "opened_by": "ada", "state": state,
        "agent": agent, "harness": harness, "title": "Add a test", "archived": False,
        "runs_on": {"kind": "team_run", "execution_id": ROOT, "seat": "coder" if "coder" in uid else "reviewer"},
        "lineage": {"forked_from": None, "imported_from": None}, "turns": turns, "started_at": NOW,
        "app": {"id": f"team-{TEAM}", "version": 1}, "instance": {"app_uid": f"team:{TEAM}", "version": 1},
    }


def _entries(uid: str, turns: int) -> list[dict[str, Any]]:
    entries = [{"kind": "session", "at": NOW, "summary": "started", "payload": {"app": f"team-{TEAM}", "state": "open", "agent": "a", "harness": "claude-code"}}]
    for n in range(1, turns + 1):
        entries.append({"kind": "tool_call", "at": NOW, "summary": "edit", "payload": {"tool": "edit", "arguments": {"Authorization": "Bearer sk-secret-1"}}})
        entries.append({"kind": "turn", "at": NOW, "summary": f"Turn {n}", "payload": {"asked": f"Ask {n}", "answered": f"Answer {n}"}})
    return entries


class Response:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self) -> Any:
        return self._body


class World:
    """A control plane with one coding-agents run, and the record of its seats' sessions."""

    def __init__(self, *, root_state: ExecutionState = ExecutionState.COMPLETED, reviewer_state: ExecutionState = ExecutionState.COMPLETED, reviewer_session: str | None = "sess-reviewer") -> None:
        self.executions = [
            an_execution(ROOT, f"team:{TEAM}/supervisor", state=root_state),
            an_execution("exec_coder", f"team:{TEAM}/coder", parent=ROOT, depth=1),
            an_execution("exec_reviewer", f"team:{TEAM}/reviewer", parent=ROOT, depth=1, state=reviewer_state, created="2026-10-21T10:08:00Z"),
            an_execution("exec_other", "jupyter-data-analyst", root="exec_other"),
        ]
        self.attempts = {"exec_coder": [an_attempt("exec_coder", "sess-coder")], "exec_reviewer": [an_attempt("exec_reviewer", reviewer_session)]}
        self.sessions = {
            "sess-coder": (_session_row("sess-coder", agent="shield-mvp-claude-code", harness="claude-code", turns=2), _entries("sess-coder", 2)),
            "sess-reviewer": (_session_row("sess-reviewer", agent="shield-mvp-codex", harness="codex", turns=1, state="open" if reviewer_state is ExecutionState.RUNNING else "paused"), _entries("sess-reviewer", 1)),
        }
        self.recorded: dict[str, Any] = {"written": [], "copied": [], "resumed": [], "terminated": [], "steered": []}
        self.urls = SimpleNamespace(ai_agents_url="https://ai-agents.test")

    # -- the control plane --
    def list_executions(self, *, root_execution_id: str | None = None, account_uid: str | None = None, **_: Any) -> list[Execution]:
        return [one for one in self.executions if not root_execution_id or one.root_execution_id == root_execution_id]

    def get_execution(self, execution_id: str, *, account_uid: str | None = None) -> Any:
        execution = next(one for one in self.executions if one.execution_id == execution_id)
        return SimpleNamespace(execution=execution, attempts=self.attempts.get(execution_id, []), acknowledgements=[])

    def _receipt(self, kind: str, command: Any) -> Any:
        self.recorded[kind].append(command)
        root = self.executions[0]
        return SimpleNamespace(execution=root, delivered=True, detail=None, to_wire=lambda: {"execution": root.to_wire(), "delivered": True})

    def resume_execution(self, command: Any, *, account_uid: str | None = None) -> Any:
        return self._receipt("resumed", command)

    def terminate_execution(self, command: Any, *, account_uid: str | None = None) -> Any:
        return self._receipt("terminated", command)

    def steer_execution(self, command: Any, *, account_uid: str | None = None) -> Any:
        return self._receipt("steered", command)

    # -- the record --
    def _fetch(self, url: str, *, method: str = "GET", json: Any = None) -> Response:
        path = url.split("/api/ai-agents/v1", 1)[1]
        if method == "POST" and path.startswith("/orchestration/executions/from-session"):
            self.recorded.setdefault("from_session", []).append(dict(json))
            if json["team"] != TEAM:
                return Response(404, {"detail": {"code": "not_found", "message": f"No team '{json['team']}' in the catalogue."}})
            root = an_execution("exec_new_run", f"team:{TEAM}/supervisor", state=ExecutionState.CREATED, root="exec_new_run")
            return Response(202, {"success": True, "execution": root.to_wire(), "acknowledgement": {"kind": "received"}, "dispatched": True, "detail": None})
        if method == "POST" and path.startswith("/orchestration/executions/swap-seat"):
            self.recorded.setdefault("swapped", []).append(dict(json))
            return Response(200, {
                "success": True, "executionId": json["executionId"], "seat": json["seat"], "sessionUid": "sess-coder",
                "fromAgent": "shield-mvp-claude-code", "toAgent": json["agent"], "atTurn": 2, "how": "record",
                "said": "This conversation was with Claude Code until turn 2; from here it is with Codex.", "steered": True,
            })
        path = path[len("/apps"):] if path.startswith("/apps") else path
        if method == "GET" and path.startswith("/records/sessions/"):
            uid = path[len("/records/sessions/"):].split("?")[0]
            if uid not in self.sessions:
                return Response(404, {"detail": f"You had no session {uid}."})
            row, entries = self.sessions[uid]
            return Response(200, {"session": row, "entries": entries})
        if method == "POST" and path == "/records/copy":
            source = json["from"]
            if source not in self.sessions:
                return Response(404, {"detail": f"You had no session {source}."})
            row, entries = self.sessions[source]
            at_turn = json.get("up_to_turn") or row["turns"]
            if at_turn > row["turns"]:
                return Response(422, {"detail": f"Session {source} has {row['turns']} turns; there is no turn {at_turn} to cut it at."})
            self.recorded["copied"].append(dict(json))
            clone = {**row, "uid": json["to"], "state": "paused", "turns": at_turn, "lineage": {"forked_from": {"uid": source, "at_turn": at_turn}, "imported_from": None}}
            self.sessions[json["to"]] = (clone, entries[: 1 + 2 * at_turn])
            return Response(201, {"success": True, "from": source, "to": json["to"], "at_turn": at_turn, "copied": 1 + 2 * at_turn})
        if method == "POST" and path == "/records":
            self.recorded["written"].append(dict(json))
            # Written, it is listed: a row from its head and its life, as ai-agents keeps one.
            entries = list(json["entries"])
            head = next(e["payload"] for e in entries if e["kind"] == "session")
            state = "open"
            for entry in entries:
                if entry["kind"] in ("paused", "ended"):
                    state = entry["payload"]["state"]
            row = {
                "uid": json["session_uid"], "app_uid": json["app_uid"], "owner_uid": json.get("opened_by") or "",
                "opened_by": json.get("opened_by") or "", "state": state, "agent": head["agent"], "harness": head.get("harness"),
                "title": head.get("title") or "", "archived": False, "runs_on": None, "lineage": head.get("lineage"),
                "turns": sum(1 for e in entries if e["kind"] == "turn"), "started_at": NOW,
            }
            self.sessions[json["session_uid"]] = (row, [{"at": NOW, **e} for e in entries])
            return Response(200, {"success": True})
        return Response(404, {"detail": f"No route {method} {path}"})


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> World:
    made = World()
    monkeypatch.setattr(sessions_commands, "client", lambda: made)
    monkeypatch.setattr(platform_cli, "client", lambda: made)
    monkeypatch.setenv("AGENT_RUNTIMES_SESSIONS_PATH", str(tmp_path / "sessions"))
    monkeypatch.setenv("AGENT_TEAMS_SESSIONS_PATH", str(tmp_path / "teams"))
    monkeypatch.chdir(tmp_path)
    return made


def invoke(*args: str) -> Any:
    return runner.invoke(app, ["sessions", *args])


# --- the shape (MH-22) -------------------------------------------------------------------


def test_each_execution_state_maps_to_a_session_state_and_an_unknown_one_is_refused() -> None:
    assert {word: session_state_of(word) for word in EXECUTION_TO_SESSION} == {
        "created": "open", "assigned": "open", "running": "open", "waiting": "open", "retrying": "open",
        "paused": "paused", "completed": "ended", "failed": "ended", "cancelled": "ended", "terminated": "ended",
    }
    assert session_state_of(ExecutionState.RUNNING) == "open"
    with pytest.raises(OrchestrationCommandError, match="There is no execution state 'stopped' to read a team session's state from"):
        session_state_of("stopped")


def test_a_run_with_two_seats_is_described_in_both_shapes(world: World) -> None:
    shown = json.loads(plain(invoke("show", ROOT, "-o", "json").stdout))
    assert shown["execution_id"] == ROOT and shown["team"] == {"id": TEAM, "version": "0.0.1"}
    assert shown["state"] == "ended" and shown["execution_state"] == "completed" and shown["owner"] == {"uid": "ada"}
    assert [seat["id"] for seat in shown["seats"]] == ["coder", "reviewer"]
    coder, reviewer = shown["seats"]
    assert (coder["session_uid"], coder["state"], coder["agent"], coder["harness"], coder["turns"]) == ("sess-coder", "paused", "shield-mvp-claude-code", "claude-code", 2)
    assert (reviewer["session_uid"], reviewer["agent"], reviewer["turns"]) == ("sess-reviewer", "shield-mvp-codex", 1)
    # `agent-teams status` gains each seat's session line, the same facts.
    status = json.loads(plain(runner.invoke(app, ["status", ROOT, "-o", "json"]).stdout))
    assert status["session"]["seats"] == shown["seats"]
    rows = {row["executionId"]: row for row in status["executions"]}
    assert rows["exec_coder"]["session"] == {"uid": "sess-coder", "state": "paused", "agent": "shield-mvp-claude-code", "turns": 2}
    assert rows[ROOT]["session"] is None
    text = plain(runner.invoke(app, ["status", ROOT]).stdout)
    assert "sess-coder paused shield-mvp-claude-code 2 turns" in text and "team session ended" in text


def test_the_list_shows_the_drills_run_by_team(world: World) -> None:
    listed = json.loads(plain(invoke("list", "--team", TEAM, "--all", "-o", "json").stdout))["sessions"]
    assert [one["execution_id"] for one in listed] == [ROOT]
    assert listed[0]["seats"][0]["session_uid"] == "sess-coder"
    assert json.loads(plain(invoke("list", "--team", TEAM, "-o", "json").stdout))["sessions"] == []  # ended, without --all
    text = plain(invoke("list", "--all").stdout)
    assert ROOT in text and "coder: sess-coder paused shield-mvp-claude-code (claude-code) 2 turns" in text


def test_a_run_that_is_not_a_teams_is_refused(world: World) -> None:
    result = invoke("show", "exec_other")
    assert result.exit_code != 0 and "is not a team run" in plain(result.output)


# --- resume, end, swap (MH-24, MH-25, MH-41) ---------------------------------------------


def test_resume_lets_a_paused_run_go_on_and_refuses_an_ended_one(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    ended = invoke("resume", ROOT)
    assert ended.exit_code != 0
    assert f"Team session {ROOT} ended; an ended run is not resumed — clone it to go on from it" in plain(ended.output)
    assert world.recorded["resumed"] == []
    paused = World(root_state=ExecutionState.PAUSED)
    monkeypatch.setattr(sessions_commands, "client", lambda: paused)
    monkeypatch.setattr(platform_cli, "client", lambda: paused)
    result = invoke("resume", ROOT)
    assert result.exit_code == 0, result.output
    assert "each seat's session with it" in plain(result.output)
    [command] = paused.recorded["resumed"]
    assert command.execution_id == ROOT


def test_end_terminates_the_run_so_its_seats_sessions_end_with_it(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    live = World(root_state=ExecutionState.RUNNING, reviewer_state=ExecutionState.RUNNING)
    monkeypatch.setattr(sessions_commands, "client", lambda: live)
    result = invoke("end", ROOT)
    assert result.exit_code == 0, result.output
    assert "its seats' sessions end with it" in plain(result.output)
    [command] = live.recorded["terminated"]
    assert command.execution_id == ROOT and command.release_worker is True
    monkeypatch.setattr(sessions_commands, "client", lambda: world)
    already = invoke("end", ROOT)  # the world's own run has ended
    assert already.exit_code != 0 and f"Team session {ROOT} ended already." in plain(already.output)


def test_swap_refuses_a_running_seat_a_seat_without_a_session_and_an_unknown_seat(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    live = World(root_state=ExecutionState.RUNNING, reviewer_state=ExecutionState.RUNNING)
    monkeypatch.setattr(sessions_commands, "client", lambda: live)
    running = invoke("swap", ROOT, "--seat", "reviewer", "--agent", "shield-mvp-claude-code")
    assert running.exit_code != 0
    assert "Seat reviewer is answering its task: swap it when the task is over, or interrupt it first." in plain(running.output)
    nobody = World(root_state=ExecutionState.RUNNING, reviewer_session=None)
    monkeypatch.setattr(sessions_commands, "client", lambda: nobody)
    # A seat this view sees no session for is the control plane's to judge
    # (a run opened from a team session carries its seats' sessions on its
    # root): asked, never refused here.
    asked_anyway = invoke("swap", ROOT, "--seat", "reviewer", "--agent", "shield-mvp-claude-code")
    assert asked_anyway.exit_code == 0, asked_anyway.output
    nobody.recorded["swapped"].clear()
    unknown = invoke("swap", ROOT, "--seat", "tester", "--agent", "x")
    assert unknown.exit_code != 0 and "has no seat 'tester': one of coder, reviewer" in plain(unknown.output)
    same = invoke("swap", ROOT, "--seat", "coder", "--agent", "shield-mvp-claude-code")
    assert same.exit_code != 0 and "is bound to shield-mvp-claude-code already" in plain(same.output)
    # The swap itself is the control plane's `executions.swap-seat` (decision V): asked, and its answer said.
    swapped = invoke("swap", ROOT, "--seat", "coder", "--agent", "shield-mvp-codex")
    assert swapped.exit_code == 0, swapped.output
    assert "Seat coder swapped to shield-mvp-codex in the record (session sess-coder): This conversation was with Claude Code until turn 2" in plain(swapped.output)
    assert "The supervisor was told." in plain(swapped.output)
    [asked] = nobody.recorded["swapped"]
    assert (asked["executionId"], asked["seat"], asked["agent"]) == (ROOT, "coder", "shield-mvp-codex") and asked["idempotencyKey"].startswith(f"swap:{ROOT}:coder:")


# --- clone (MH-40) -------------------------------------------------------------------------


def test_clone_copies_each_seats_session_into_a_paused_team_session_with_lineage(world: World) -> None:
    result = invoke("clone", ROOT, "--title", "Before the review", "-o", "json")
    assert result.exit_code == 0, result.output
    row = json.loads(plain(result.stdout))
    assert row["state"] == "paused" and row["lineage"] == {"forked_from": {"execution_id": ROOT}, "imported_from": None}
    assert row["title"] == "Before the review" and row["execution_id"].startswith("team-session-")
    coder, reviewer = row["seats"]
    assert coder["cloned_from"] == {"uid": "sess-coder", "at_turn": 2} and reviewer["cloned_from"] == {"uid": "sess-reviewer", "at_turn": 1}
    assert {one["from"] for one in world.recorded["copied"]} == {"sess-coder", "sess-reviewer"}
    assert coder["session_uid"] != "sess-coder" and coder["session_uid"] in world.sessions
    # Kept where the run is not: the team session's file, listed and shown; the source untouched.
    assert TeamSessionStore().get(row["execution_id"])["seats"][0]["session_uid"] == coder["session_uid"]
    assert world.sessions["sess-coder"][0]["state"] == "paused" and world.sessions["sess-coder"][0]["turns"] == 2


def test_clone_cuts_one_seat_at_a_turn_and_refuses_a_turn_it_lacks(world: World) -> None:
    result = invoke("clone", ROOT, "--seat", "coder", "--at", "1", "-o", "json")
    assert result.exit_code == 0, result.output
    row = json.loads(plain(result.stdout))
    assert row["seats"][0]["cloned_from"] == {"uid": "sess-coder", "at_turn": 1}
    assert row["seats"][1]["cloned_from"] == {"uid": "sess-reviewer", "at_turn": 1}
    lacks = invoke("clone", ROOT, "--seat", "coder", "--at", "7")
    assert lacks.exit_code != 0 and "Seat coder's session has 2 turns; there is no turn 7 to clone it at." in plain(lacks.output)
    half = invoke("clone", ROOT, "--seat", "coder")
    assert half.exit_code != 0 and "`--seat` and `--at` go together" in plain(half.output)


# --- export and import (MH-24, MH-39) ----------------------------------------------------


def test_export_writes_the_team_line_then_each_seats_session_redacted(world: World, tmp_path: Path) -> None:
    out = tmp_path / "run.team-session.jsonl"
    result = invoke("export", ROOT, "--out", str(out))
    assert result.exit_code == 0, result.output
    assert "Exported 2 seats, 3 turns" in plain(result.output) and "redactions" in plain(result.output)
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    head = lines[0]
    assert head["record_type"] == "team_session" and head["format"] == "datalayer-session/1"
    assert head["execution_id"] == ROOT and head["team"] == {"id": TEAM, "version": "0.0.1"} and head["state"] == "ended"
    assert head["seats"] == [
        {"id": "coder", "session_uid": "sess-coder", "agent": "shield-mvp-claude-code", "harness": "claude-code"},
        {"id": "reviewer", "session_uid": "sess-reviewer", "agent": "shield-mvp-codex", "harness": "codex"},
    ]
    sessions = [line for line in lines if line["record_type"] == "session"]
    assert [one["uid"] for one in sessions] == ["sess-coder", "sess-reviewer"]
    # The bearer in a tool row is redacted and counted.
    entries = [line for line in lines if line["record_type"] == "entry"]
    assert all("sk-secret-1" not in json.dumps(line) for line in entries)
    assert head["redactions"] == 3 and sum(one["redactions"] for one in sessions) == 3
    from agentspecs.sessions import parse_line

    for number, line in enumerate(lines, 1):
        parse_line(line, line=number)


def test_export_refuses_a_run_whose_seat_kept_no_session(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    nobody = World(reviewer_session=None)
    monkeypatch.setattr(sessions_commands, "client", lambda: nobody)
    result = invoke("export", ROOT)
    assert result.exit_code != 0
    assert f"Team session {ROOT} cannot be exported: its seat reviewer kept no session." in plain(result.output)


def test_import_makes_a_new_paused_team_session_whose_seats_are_new_sessions(world: World, tmp_path: Path) -> None:
    out = tmp_path / "run.team-session.jsonl"
    assert invoke("export", ROOT, "--out", str(out)).exit_code == 0
    result = invoke("import", str(out), "-o", "json")
    assert result.exit_code == 0, result.output
    row = json.loads(plain(result.stdout))
    assert row["state"] == "paused" and row["team"]["id"] == TEAM
    assert row["lineage"]["forked_from"] is None and row["lineage"]["imported_from"]["digest"].startswith("sha256:")
    assert [one["id"] for one in row["seats"]] == ["coder", "reviewer"]
    assert [one["imported_from"] for one in row["seats"]] == ["sess-coder", "sess-reviewer"]
    assert all(one["session_uid"] not in ("sess-coder", "sess-reviewer") for one in row["seats"])
    # Each seat's session written to the record as a new one, the importer its author, imported and paused.
    written = {one["session_uid"]: one for one in world.recorded["written"]}
    assert set(written) == {one["session_uid"] for one in row["seats"]}
    coder = written[row["seats"][0]["session_uid"]]
    kinds = [entry["kind"] for entry in coder["entries"]]
    assert kinds[0] == "session" and kinds[-2:] == ["imported", "paused"] and kinds.count("turn") == 2
    assert coder["entries"][0]["payload"]["lineage"]["imported_from"]["digest"] == row["lineage"]["imported_from"]["digest"]
    assert coder["entries"][0]["payload"]["runs_on"] == {"kind": "team_run", "execution_id": "", "seat": "coder"}
    assert coder["entries"][-2]["payload"]["source_uid"] == "sess-coder"
    assert TeamSessionStore().get(row["execution_id"])["state"] == "paused"
    # Resumed: a new root carrying the seats' sessions, through the control plane's
    # `executions.from-session` (decision U); the file then names the run.
    no_goal = invoke("resume", row["execution_id"])
    assert no_goal.exit_code != 0 and "has no goal to run on: say one with `--goal`" in plain(no_goal.output)
    resumed = invoke("resume", row["execution_id"], "--goal", "Go on with the review")
    assert resumed.exit_code == 0, resumed.output
    assert f"Opened run exec_new_run from team session {row['execution_id']}: each seat goes on from its session (coder: " in plain(resumed.output)
    [asked] = world.recorded["from_session"]
    assert asked["team"] == TEAM and asked["objective"] == {"goal": "Go on with the review"}
    assert asked["sessions"] == {one["id"]: one["session_uid"] for one in row["seats"]}
    kept = TeamSessionStore().get(row["execution_id"])
    assert kept["state"] == "open" and kept["run_execution_id"] == "exec_new_run"
    again = invoke("resume", row["execution_id"], "--goal", "Again")
    assert again.exit_code != 0 and "already runs as exec_new_run" in plain(again.output)


def test_import_refuses_an_unknown_team_and_a_file_that_is_one_session(world: World, tmp_path: Path) -> None:
    out = tmp_path / "run.team-session.jsonl"
    assert invoke("export", ROOT, "--out", str(out)).exit_code == 0
    lines = out.read_text().splitlines()
    head = json.loads(lines[0])
    head["team"]["id"] = "no-such-team"
    (tmp_path / "unknown.jsonl").write_text("\n".join([json.dumps(head), *lines[1:]]) + "\n")
    unknown = invoke("import", str(tmp_path / "unknown.jsonl"))
    assert unknown.exit_code != 0 and "No team 'no-such-team' is catalogued" in plain(unknown.output)
    (tmp_path / "one.jsonl").write_text("\n".join(lines[1:]) + "\n")
    one = invoke("import", str(tmp_path / "one.jsonl"))
    assert one.exit_code != 0 and "import it with `loop sessions import`" in plain(one.output)
    missing = invoke("import", str(tmp_path / "none.jsonl"))
    assert missing.exit_code != 0 and "There is no file" in plain(missing.output)


# --- the local stores (--local) --------------------------------------------------------------


def test_the_local_verbs_read_a_self_hosted_servers_files(world: World) -> None:
    store, teams = FileSessionStore(), TeamSessionStore()
    store.append("local-coder", _entries("local-coder", 2), app_uid=f"team:{TEAM}", opened_by="me")
    teams.put({"execution_id": "run-local-1", "team": {"id": TEAM, "version": "0.0.1"}, "owner": {"uid": "me"}, "state": "open", "title": "", "seats": [{"id": "coder", "session_uid": "local-coder", "agent": "a", "harness": "claude-code"}], "lineage": {"forked_from": None, "imported_from": None}, "started_at": NOW})
    listed = json.loads(plain(invoke("list", "--local", "-o", "json").stdout))["sessions"]
    assert [one["execution_id"] for one in listed] == ["run-local-1"] and listed[0]["seats"][0]["turns"] == 2
    cloned = json.loads(plain(invoke("clone", "run-local-1", "--local", "-o", "json").stdout))
    assert cloned["state"] == "paused" and cloned["seats"][0]["cloned_from"] == {"uid": "local-coder", "at_turn": 2}
    assert store.describe(cloned["seats"][0]["session_uid"])["lineage"]["forked_from"] == {"uid": "local-coder", "at_turn": 2}
    ended = invoke("end", "run-local-1", "--local")
    assert ended.exit_code == 0 and teams.get("run-local-1")["state"] == "ended" and store.describe("local-coder")["state"] == "ended"
    resume = invoke("resume", "run-local-1", "--local")
    assert resume.exit_code != 0 and "clone it to go on" in plain(resume.output)
