# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""``agent-teams sessions …``: a team run as a session (MH-22, MH-41).

A team session is one run of a team — the root execution the control plane
runs it under, its id the session's — that owns one session per seat, kept
across the run's tasks. The verbs are ``loop sessions …``'s, at the team's
level, one API shape at both levels: a team session row is the run (its
execution id, team, owner, state, lineage) plus its seats, each with its
session as the record lists it (uid, state, agent, harness, turns).

- ``list [--team] [--all]``, ``show <execution>``: the caller's team sessions,
  the run's state mapped from the execution's (``created``, ``assigned``,
  ``running``, ``waiting``, ``retrying`` → ``open``; ``paused`` → ``paused``;
  ``completed``, ``failed``, ``cancelled``, ``terminated`` → ``ended``; any
  other word refused), the seats' sessions read from the session API by the
  uids the run carries (each seat's child execution's attempt, MH-P05) — the
  control plane keeps no copy;
- ``resume <execution>``: the run goes on (``executions.resume``) and each
  seat's session with it; an ended run is not resumed — clone it to go on;
- ``swap <execution> --seat <id> --agent <spec>``: the seat's session swapped
  and the supervisor steered with the sentence; refused while the seat's turn
  runs (409), for a seat whose session is not the run's (422);
- ``clone <execution> [--title] [--seat <id> --at <turn>]``: a new team session,
  ``paused``, each seat's session cloned (the record's copy, MH-P02), no
  workspace forked;
- ``export <execution> [-o file]``: the team line, then each seat's session
  (``datalayer-session/1``), refused naming the seats that kept no session;
- ``import <file>``: the inverse, a new team session ``paused`` whose seats
  are the file's sessions imported as new ones, the importer their author;
- ``end <execution>``: the run ended and its seats' sessions with it.

``--local`` reads and writes a self-hosted server's stores (the files of
``agent_teams.sessions``) instead of the platform's record. Every refusal is
a sentence that says what to do instead; nothing falls back to a default.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import typer
from datalayer_core.cli.commands.contents import OutputFormat
from datalayer_core.cli.commands.orchestration_common import (
    OrchestrationCommandError,
    account_option,
    call,
    client,
    console,
    emit,
    orchestration_command,
    output_option,
)
from datalayer_core.orchestration import (
    Execution,
    ExecutionsResume,
    ExecutionsTerminate,
)
from rich.table import Table

from .sessions import (
    FileSessionStore,
    SessionRefused,
    TeamSessionStore,
    check_state,
    entries_up_to_turn,
    new_uid,
)

#: The session's state a run's execution state maps to (MH-22); any other word is refused.
EXECUTION_TO_SESSION: Mapping[str, str] = {
    "created": "open",
    "assigned": "open",
    "running": "open",
    "waiting": "open",
    "retrying": "open",
    "paused": "paused",
    "completed": "ended",
    "failed": "ended",
    "cancelled": "ended",
    "terminated": "ended",
}

#: The ai-agents routes the verbs read and write the record through.
APPS = "/api/ai-agents/v1/apps"

#: The control plane's routes the verbs open a run from a team session and swap a seat through.
ORCHESTRATION = "/api/ai-agents/v1/orchestration"

#: The file format a team session travels as.
FORMAT = "datalayer-session/1"

#: Replaced by the key derived from the command before it is sent.
_PENDING_KEY = "pending"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def session_state_of(word: Any) -> str:
    """The team session's state from the run's execution state, or a refusal naming the words."""
    said = str(getattr(word, "value", word) or "")
    state = EXECUTION_TO_SESSION.get(said)
    if state is None:
        raise OrchestrationCommandError(
            f"There is no execution state {said!r} to read a team session's state from: one of "
            + ", ".join(EXECUTION_TO_SESSION)
            + "."
        )
    return state


def team_of(execution: Execution) -> tuple[str, str]:
    """``(team, seat)`` of an execution whose agent id names a seat; ``("", "")`` otherwise."""
    agent_id = execution.agent.agent_id
    if not agent_id.startswith("team:"):
        return "", ""
    team, _, seat = agent_id[len("team:") :].partition("/")
    return team, seat


def _catalogue_team(team_id: str) -> Any:
    """The catalogued team spec, or a refusal (422) that names the catalogue."""
    try:
        from agent_runtimes.specs import teams
    except ImportError as error:
        raise OrchestrationCommandError(
            "The team catalogue ships with agent-runtimes: `pip install agent-runtimes` to read a team's seats."
        ) from error
    team = teams.get_team_spec(team_id)
    if team is None:
        raise OrchestrationCommandError(
            f"No team '{team_id}' is catalogued: `agent-teams list` lists them."
        )
    return team


def _format() -> Any:
    """The ``datalayer-session/1`` exporter and importer, which agent-runtimes ships."""
    try:
        from agent_runtimes.loop.apps.session_files import DATALAYER_SESSION_FORMAT
    except ImportError as error:
        raise OrchestrationCommandError(
            "A session file is written and read by agent-runtimes' datalayer-session/1 format: "
            "`pip install agent-runtimes` to export or import a team session."
        ) from error
    return DATALAYER_SESSION_FORMAT


# ---------------------------------------------------------------------------
# Where the seats' sessions are: the platform's record, or the local files
# ---------------------------------------------------------------------------


class PlatformRecord:
    """The seats' sessions as ai-agents keeps them, read and written as the person."""

    def __init__(self, the_client: Any, account: str | None) -> None:
        self.client = the_client
        self.account = account
        self.base = f"{str(self.client.urls.ai_agents_url).rstrip('/')}{APPS}"

    def _answer(self, response: Any, what: str) -> dict[str, Any]:
        status = int(getattr(response, "status_code", 200) or 200)
        if status >= 300:
            try:
                detail = response.json().get("detail") or response.text
            except Exception:
                detail = getattr(response, "text", "")
            raise OrchestrationCommandError(f"{what} ({status}): {str(detail)[:400]}")
        return dict(response.json() or {})

    def session(self, uid: str, up_to_turn: int | None = None) -> dict[str, Any]:
        """``{session: row, entries}`` of one session the person opened."""
        query = f"?up_to_turn={up_to_turn}" if up_to_turn else ""
        return self._answer(
            call(
                lambda: self.client._fetch(
                    f"{self.base}/records/sessions/{uid}{query}", method="GET"
                )
            ),
            f"Session {uid} could not be read",
        )

    def write(
        self, uid: str, app_uid: str, entries: Sequence[Mapping[str, Any]], *, opened_by: str = ""
    ) -> dict[str, Any]:
        body = {
            "app_uid": app_uid,
            "session_uid": uid,
            "entries": list(entries),
            "opened_by": opened_by,
        }
        return self._answer(
            call(lambda: self.client._fetch(f"{self.base}/records", method="POST", json=body)),
            f"Session {uid} could not be written",
        )

    def copy(
        self, source: str, target: str, *, up_to_turn: int | None = None, title: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"from": source, "to": target}
        if up_to_turn:
            body["up_to_turn"] = up_to_turn
        if title:
            body["title"] = title
        return self._answer(
            call(lambda: self.client._fetch(f"{self.base}/records/copy", method="POST", json=body)),
            f"Session {source} could not be cloned",
        )


class LocalRecord:
    """The seats' sessions in a self-hosted server's files."""

    def __init__(self) -> None:
        self.store = FileSessionStore()
        self.teams = TeamSessionStore()

    def session(self, uid: str, up_to_turn: int | None = None) -> dict[str, Any]:
        row = self.store.describe(uid)
        if row is None:
            raise OrchestrationCommandError(f"There is no session {uid} in the local store.")
        try:
            entries = entries_up_to_turn(self.store.entries(uid), up_to_turn)
        except SessionRefused as refused:
            raise OrchestrationCommandError(refused.reason) from None
        return {"session": row, "entries": entries}

    def write(
        self, uid: str, app_uid: str, entries: Sequence[Mapping[str, Any]], *, opened_by: str = ""
    ) -> dict[str, Any]:
        self.store.append(uid, entries, app_uid=app_uid, opened_by=opened_by)
        return {"session_uid": uid, "written": len(entries)}

    def copy(
        self, source: str, target: str, *, up_to_turn: int | None = None, title: str | None = None
    ) -> dict[str, Any]:
        try:
            copied = self.store.copy(source, target, up_to_turn=up_to_turn)
        except SessionRefused as refused:
            raise OrchestrationCommandError(refused.reason) from None
        at_turn = up_to_turn or sum(
            1 for e in self.store.entries(source) if e.get("kind") == "turn"
        )
        self.store.append(
            source,
            [
                {
                    "kind": "cloned",
                    "summary": f"Cloned as {target} at turn {at_turn}",
                    "payload": {"to": target, "at_turn": at_turn},
                }
            ],
        )
        entries: list[dict[str, Any]] = [
            {
                "kind": "cloned",
                "summary": f"Cloned from {source} at turn {at_turn}",
                "payload": {"from": source, "at_turn": at_turn},
            }
        ]
        if title:
            entries.append(
                {"kind": "titled", "summary": title, "payload": {"title": title, "archived": False}}
            )
        self.store.append(target, entries)
        return {"from": source, "to": target, "at_turn": at_turn, "copied": copied}


# ---------------------------------------------------------------------------
# The team session row (MH-22)
# ---------------------------------------------------------------------------


def seat_row(seat: str, session: Mapping[str, Any] | None, uid: str = "") -> dict[str, Any]:
    """One seat of a team session, with its session as the record lists it, or none."""
    if session is None:
        return {
            "id": seat,
            "session_uid": uid or None,
            "state": None,
            "agent": None,
            "harness": None,
            "turns": 0,
        }
    return {
        "id": seat,
        "session_uid": str(session.get("uid") or uid),
        "state": check_state(str(session.get("state") or "")),
        "agent": str(session.get("agent") or ""),
        "harness": session.get("harness") or None,
        "turns": int(session.get("turns") or 0),
        "title": str(session.get("title") or ""),
    }


def platform_team_session(
    the_client: Any,
    root: Execution,
    tree: Sequence[Execution],
    record: PlatformRecord,
    account: str | None,
) -> dict[str, Any]:
    """The run as a team session: its state from the execution's, its seats' sessions from the record."""
    team_id, seat = team_of(root)
    if not team_id or seat != "supervisor" or root.parent_execution_id:
        raise OrchestrationCommandError(
            f"Execution {root.execution_id} is not a team run (its agent is {root.agent.agent_id}): "
            "`agent-teams runs` lists the runs."
        )
    team = _catalogue_team(team_id)
    latest: dict[str, tuple[str, str | None]] = {}
    for child in sorted(tree, key=lambda one: str(one.created_at)):
        _, place = team_of(child)
        if child.execution_id == root.execution_id or not place or place == "supervisor":
            continue
        uid = None
        child_id = child.execution_id
        for attempt in call(
            lambda: the_client.get_execution(child_id, account_uid=account)  # noqa: B023 - bound above
        ).attempts:
            if attempt.session_id:
                uid = attempt.session_id
        state = getattr(child.status, "value", str(child.status))
        kept = latest.get(place)
        latest[place] = (state, uid or (kept[1] if kept else None))
    seats: list[dict[str, Any]] = []
    owner = ""
    for member in team.agents:
        state, uid = latest.get(member.id, ("", None))
        if uid:
            read = record.session(uid)
            row = seat_row(member.id, read["session"], uid)
            owner = owner or str(
                read["session"].get("opened_by") or read["session"].get("owner_uid") or ""
            )
        else:
            row = seat_row(member.id, None)
        row["execution_state"] = state
        row["turn"] = "running" if state in ("running", "waiting") else "idle"
        seats.append(row)
    return {
        "execution_id": root.execution_id,
        "team": {"id": team.id, "version": str(team.version or "0")},
        "owner": {"uid": owner},
        "state": session_state_of(root.status),
        "execution_state": getattr(root.status, "value", str(root.status)),
        "title": root.objective.goal[:200],
        "seats": seats,
        "lineage": {"forked_from": None, "imported_from": None},
        "started_at": str(root.created_at),
    }


def local_team_session(record: LocalRecord, execution_id: str) -> dict[str, Any]:
    """A team session of the local files, its seats' sessions read from the file store."""
    kept = kept_team_session(record, execution_id)
    if kept is None:
        raise OrchestrationCommandError(
            f"There is no team session {execution_id} in the local store: "
            "`agent-teams sessions list --local` lists them."
        )
    return kept


# ---------------------------------------------------------------------------
# What the verbs share
# ---------------------------------------------------------------------------


def _record(local: bool, account: str | None) -> Any:
    return LocalRecord() if local else PlatformRecord(client(), account)


def _run(
    the_client: Any, execution_id: str, account: str | None
) -> tuple[Execution, list[Execution]]:
    root = call(lambda: the_client.get_execution(execution_id, account_uid=account)).execution
    tree = call(
        lambda: the_client.list_executions(
            root_execution_id=root.root_execution_id, account_uid=account
        )
    )
    return root, list(tree)


def kept_team_session(record: Any, execution_id: str) -> dict[str, Any] | None:
    """A team session kept as a file — a self-hosted run, a clone or an import
    waiting for its run — with its seats' sessions read through `record`; `None`
    when no file says so."""
    row = TeamSessionStore().get(execution_id)
    if row is None:
        return None
    seats = []
    for seat in row.get("seats") or []:
        uid = str(seat.get("session_uid") or "")
        described = record.session(uid)["session"] if uid else None
        seats.append(seat_row(str(seat["id"]), described, uid))
    return {**row, "state": check_state(str(row.get("state") or "")), "seats": seats}


def _session(local: bool, execution_id: str, account: str | None) -> tuple[dict[str, Any], Any]:
    record = _record(local, account)
    kept = kept_team_session(record, execution_id)
    if kept is not None:
        return kept, record
    if local:
        raise OrchestrationCommandError(
            f"There is no team session {execution_id} in the local store: "
            "`agent-teams sessions list --local` lists them."
        )
    the_client = record.client
    root, tree = _run(the_client, execution_id, account)
    return platform_team_session(the_client, root, tree, record, account), record


def _seat_words(seat: Mapping[str, Any]) -> str:
    if not seat.get("session_uid"):
        return f"{seat['id']}: no session yet"
    return (
        f"{seat['id']}: {seat['session_uid']} {seat['state']} {seat['agent']}"
        + (f" ({seat['harness']})" if seat.get("harness") else "")
        + f" {seat['turns']} turns"
    )


def _print_session(session: Mapping[str, Any]) -> None:
    lineage = session.get("lineage") or {}
    where = ""
    if lineage.get("forked_from"):
        where = f" · cloned from {lineage['forked_from'].get('execution_id')}"
    elif lineage.get("imported_from"):
        where = f" · imported from a file exported {lineage['imported_from'].get('exported_at')}"
    console.print(
        f"{session['execution_id']} · {session['team']['id']} · {session['state']}"
        + (f" ({session['execution_state']})" if session.get("execution_state") else "")
        + where,
        markup=False,
        highlight=False,
    )
    table = Table()
    for column in ("Seat", "Session", "State", "Agent", "Harness", "Turns"):
        table.add_column(column, overflow="fold")
    for seat in session["seats"]:
        table.add_row(
            str(seat["id"]),
            str(seat.get("session_uid") or "—"),
            str(seat.get("state") or "no session yet"),
            str(seat.get("agent") or ""),
            str(seat.get("harness") or ""),
            str(seat.get("turns") or 0),
        )
    console.print(table)


def local_option() -> Any:
    return typer.Option(
        False,
        "--local",
        help="A self-hosted server's sessions, in its files, instead of Datalayer's record.",
    )


# ---------------------------------------------------------------------------
# The verbs
# ---------------------------------------------------------------------------

sessions_app = typer.Typer(
    name="sessions",
    help=(
        "A team run as a session: list, show, resume, swap a seat, clone, export, import, end — "
        "the same verbs as `loop sessions`, at the team's level."
    ),
    no_args_is_help=True,
)


@sessions_app.command("list")
@orchestration_command
def list_sessions(
    team_id: Optional[str] = typer.Option(None, "--team", help="Only this team's sessions."),
    all_sessions: bool = typer.Option(
        False, "--all", help="Ended runs too; the ones not over otherwise."
    ),
    limit: int = typer.Option(20, "--limit", "-n", min=1, help="The latest this many."),
    local: bool = local_option(),
    account: Optional[str] = account_option(),
    output: OutputFormat = output_option(),
) -> None:
    """The caller's team sessions: each run, its state, its seats with their sessions."""
    rows: list[dict[str, Any]] = []
    if local:
        record = LocalRecord()
        for row in record.teams.list(team=team_id or ""):
            session = local_team_session(record, str(row["execution_id"]))
            if all_sessions or session["state"] != "ended":
                rows.append(session)
    else:
        record = PlatformRecord(client(), account)
        listed = call(lambda: record.client.list_executions(account_uid=account))
        roots = [
            one
            for one in listed
            if one.depth == 0
            and team_of(one)[1] == "supervisor"
            and (not team_id or team_of(one)[0] == team_id)
        ]
        roots = sorted(roots, key=lambda one: str(one.created_at), reverse=True)
        for root in roots:
            if not all_sessions and session_state_of(root.status) == "ended":
                continue
            tree = [one for one in listed if one.root_execution_id == root.execution_id]
            rows.append(platform_team_session(record.client, root, tree, record, account))
            if len(rows) >= limit:
                break
    rows = rows[:limit]
    if emit({"sessions": rows}, output):
        return
    table = Table(title=f"{len(rows)} team sessions")
    for column in ("Execution", "Team", "State", "Seats", "Started"):
        table.add_column(column, overflow="fold")
    for row in rows:
        table.add_row(
            row["execution_id"],
            row["team"]["id"],
            row["state"],
            "; ".join(_seat_words(seat) for seat in row["seats"]),
            str(row.get("started_at") or "")[:19],
        )
    console.print(table)


@sessions_app.command("show")
@orchestration_command
def show(
    execution_id: str = typer.Argument(
        ..., help="The run: its root execution, as `agent-teams sessions list` lists it."
    ),
    local: bool = local_option(),
    account: Optional[str] = account_option(),
    output: OutputFormat = output_option(),
) -> None:
    """One team session: the run, its state and lineage, each seat with its session."""
    session, _ = _session(local, execution_id, account)
    if emit(session, output):
        return
    _print_session(session)


@sessions_app.command("resume")
@orchestration_command
def resume(
    execution_id: str = typer.Argument(..., help="The paused run: its root execution."),
    goal: Optional[str] = typer.Option(
        None,
        "--goal",
        help="For a cloned or imported team session: the goal its new run works on; the kept title when omitted.",
    ),
    local: bool = local_option(),
    account: Optional[str] = account_option(),
    output: OutputFormat = output_option(),
) -> None:
    """Let a paused run go on: the run and each seat's session with it; a clone or an import gets its run."""
    session, record = _session(local, execution_id, account)
    if session["state"] == "ended":
        raise OrchestrationCommandError(
            f"Team session {execution_id} ended; an ended run is not resumed — clone it to go on from it "
            "(`agent-teams sessions clone`)."
        )
    if local:
        raise OrchestrationCommandError(
            f"Team session {execution_id} is a self-hosted server's: resume its run there "
            f"(`agent-teams local resume {session['team']['id']}`); its seats' sessions go on with it."
        )
    if not session.get("execution_state"):
        _resume_as_a_new_run(session, record, execution_id, goal, account, output)
        return
    if session["state"] == "open":
        raise OrchestrationCommandError(f"Team session {execution_id} is open, not paused.")
    command = ExecutionsResume(
        idempotency_key=_PENDING_KEY, execution_id=execution_id, checkpoint_id=None
    )
    from datalayer_core.cli.commands.executions import keyed, show_receipt

    receipt = call(
        lambda: record.client.resume_execution(keyed(command, None), account_uid=account)
    )
    show_receipt(
        receipt,
        output,
        delivered="Received, and the team goes on: each seat's session with it.",
        undelivered="Received; it has not gone on yet.",
    )


def _resume_as_a_new_run(
    session: Mapping[str, Any],
    record: Any,
    execution_id: str,
    goal: Optional[str],
    account: Optional[str],
    output: OutputFormat,
) -> None:
    """A cloned or imported team session, kept as a file, gets its run: a new
    root execution of the team delegated through the control plane's
    `executions.from-session`, carrying each seat's session uid so the seats go
    on from them at their first task (MH-39, MH-40). The file then names the run."""
    if session.get("run_execution_id"):
        raise OrchestrationCommandError(
            f"Team session {execution_id} already runs as {session['run_execution_id']}: "
            f"`agent-teams sessions show {session['run_execution_id']}` follows it."
        )
    sessions = {
        str(one["id"]): str(one["session_uid"]) for one in session["seats"] if one.get("session_uid")
    }
    if not sessions:
        raise OrchestrationCommandError(
            f"Team session {execution_id} has no seat session to go on from: there is nothing to run."
        )
    objective = (goal or "").strip() or str(session.get("title") or "").strip()
    if not objective:
        raise OrchestrationCommandError(
            f"Team session {execution_id} has no goal to run on: say one with `--goal`."
        )
    the_client = record.client
    url = f"{str(the_client.urls.ai_agents_url).rstrip('/')}{ORCHESTRATION}/executions/from-session"
    if account:
        url += f"?account_uid={account}"
    body = {
        "idempotencyKey": f"team-session:{execution_id}:{uuid.uuid4().hex[:12]}",
        "team": str(session["team"]["id"]),
        "objective": {"goal": objective},
        "sessions": sessions,
    }
    response = call(lambda: the_client._fetch(url, method="POST", json=body))
    if response.status_code >= 400:
        detail = _detail(response)
        raise OrchestrationCommandError(
            f"Team session {execution_id} was not opened as a run ({response.status_code}): {detail}"
        )
    answer = response.json()
    run_id = str((answer.get("execution") or {}).get("executionId") or "")
    kept = TeamSessionStore().get(execution_id)
    if kept is not None:
        TeamSessionStore().put({**kept, "state": "open", "run_execution_id": run_id, "resumed_at": _now()})
    shown = {
        "execution_id": execution_id,
        "run_execution_id": run_id,
        "state": "open",
        "sessions": sessions,
        "dispatched": bool(answer.get("dispatched")),
        "detail": answer.get("detail"),
    }
    if emit(shown, output):
        return
    console.print(
        f"Opened run {run_id} from team session {execution_id}: each seat goes on from its session ("
        + ", ".join(f"{seat}: {uid}" for seat, uid in sessions.items())
        + ")."
        + ("" if answer.get("dispatched") else f" Not dispatched yet: {answer.get('detail') or 'no detail'}."),
        markup=False,
        highlight=False,
    )


def _detail(response: Any) -> str:
    """What a refusal says, whatever shape it came in."""
    try:
        body = response.json()
    except ValueError:
        return str(getattr(response, "text", "") or "")[:200]
    detail = body.get("detail") if isinstance(body, dict) else body
    if isinstance(detail, dict):
        return str(detail.get("message") or detail)
    return str(detail)


@sessions_app.command("swap")
@orchestration_command
def swap(
    execution_id: str = typer.Argument(..., help="The run: its root execution."),
    seat: str = typer.Option(..., "--seat", help="The seat whose session is swapped."),
    agent: str = typer.Option(
        ..., "--agent", help="The agentspec the seat's session is bound to from here."
    ),
    local: bool = local_option(),
    account: Optional[str] = account_option(),
    output: OutputFormat = output_option(),
) -> None:
    """Swap a seat's agent in place and tell the supervisor; refused while the seat's turn runs."""
    session, record = _session(local, execution_id, account)
    place = next((one for one in session["seats"] if one["id"] == seat), None)
    if place is None:
        raise OrchestrationCommandError(
            f"Team {session['team']['id']} has no seat {seat!r}: one of "
            + ", ".join(one["id"] for one in session["seats"])
            + "."
        )
    if session["state"] == "ended":
        raise OrchestrationCommandError(
            f"Team session {execution_id} ended: clone it to go on from it, then swap the clone's seat."
        )
    # On the platform the control plane decides: a run opened from a team
    # session (a clone's, an import's) carries its seats' sessions on its
    # root before any seat's task answers one, which this view does not read.
    if local and not place.get("session_uid"):
        raise OrchestrationCommandError(
            f"Seat {seat}'s session is not the run's: it kept none (a remote worker's session is its own); "
            "only a harness or a model seat is swapped."
        )
    if place.get("turn") == "running":
        raise OrchestrationCommandError(
            f"Seat {seat} is answering its task: swap it when the task is over, or interrupt it first."
        )
    if place.get("agent") == agent:
        raise OrchestrationCommandError(f"Seat {seat}'s session is bound to {agent} already.")
    if local:
        raise OrchestrationCommandError(
            f"Seat {seat}'s session is a self-hosted server's: its runner holds the agent's process, "
            "and a swap there is not built yet."
        )
    if not session.get("execution_state"):
        raise OrchestrationCommandError(
            f"Team session {execution_id} has no run yet: resume it (`agent-teams sessions resume`), "
            "then swap the run's seat."
        )
    # The swap itself runs where the seat's session is — the control plane
    # reaches the seat's runtime, or the record — through `executions.swap-seat`
    # (MH-25, decision V); the supervisor is told in the sentence.
    the_client = record.client
    url = f"{str(the_client.urls.ai_agents_url).rstrip('/')}{ORCHESTRATION}/executions/swap-seat"
    if account:
        url += f"?account_uid={account}"
    body = {
        "idempotencyKey": f"swap:{execution_id}:{seat}:{uuid.uuid4().hex[:12]}",
        "executionId": execution_id,
        "seat": seat,
        "agent": agent,
    }
    response = call(lambda: the_client._fetch(url, method="POST", json=body))
    if response.status_code >= 400:
        raise OrchestrationCommandError(
            f"Seat {seat} was not swapped ({response.status_code}): {_detail(response)}"
        )
    answer = response.json()
    if emit(answer, output):
        return
    where = "on its runtime" if answer.get("how") == "runtime" else "in the record"
    console.print(
        f"Seat {seat} swapped to {answer.get('toAgent') or agent} {where}"
        + (f" (session {answer['sessionUid']})" if answer.get("sessionUid") else "")
        + f": {answer.get('said') or 'no sentence answered'}"
        + (
            " The supervisor was told."
            if answer.get("steered")
            else " The supervisor waits on its members: it is told in its brief when it is asked."
            if answer.get("briefed")
            else " The supervisor was not told; steer it with `agent-teams steer`."
        ),
        markup=False,
        highlight=False,
    )


@sessions_app.command("clone")
@orchestration_command
def clone(
    execution_id: str = typer.Argument(..., help="The run to clone: its root execution."),
    title: Optional[str] = typer.Option(None, "--title", help="The clone's title."),
    seat: Optional[str] = typer.Option(
        None, "--seat", help="The one seat cloned up to --at; every seat whole otherwise."
    ),
    at_turn: Optional[int] = typer.Option(
        None, "--at", min=1, help="The turn the named seat's session is cut at."
    ),
    local: bool = local_option(),
    account: Optional[str] = account_option(),
    output: OutputFormat = output_option(),
) -> None:
    """A new team session, paused: each seat's session cloned, no workspace forked."""
    if (seat is None) != (at_turn is None):
        raise OrchestrationCommandError(
            "`--seat` and `--at` go together: the seat, and the turn its session is cut at."
        )
    session, record = _session(local, execution_id, account)
    if seat is not None and all(one["id"] != seat for one in session["seats"]):
        raise OrchestrationCommandError(
            f"Team {session['team']['id']} has no seat {seat!r}: one of "
            + ", ".join(one["id"] for one in session["seats"])
            + "."
        )
    if seat is not None:
        place = next(one for one in session["seats"] if one["id"] == seat)
        if not place.get("session_uid"):
            raise OrchestrationCommandError(
                f"Seat {seat} kept no session: there is nothing to cut at turn {at_turn}."
            )
        if at_turn > int(place.get("turns") or 0):
            raise OrchestrationCommandError(
                f"Seat {seat}'s session has {place.get('turns') or 0} turns; there is no turn {at_turn} to clone it at."
            )
    new_execution = f"team-session-{uuid.uuid4().hex[:12]}"
    seats: list[dict[str, Any]] = []
    for place in session["seats"]:
        if not place.get("session_uid"):
            seats.append(
                {
                    "id": place["id"],
                    "session_uid": None,
                    "agent": place.get("agent"),
                    "harness": place.get("harness"),
                }
            )
            continue
        target = new_uid()
        copied = record.copy(
            str(place["session_uid"]),
            target,
            up_to_turn=at_turn if seat == place["id"] else None,
            title=title,
        )
        seats.append(
            {
                "id": place["id"],
                "session_uid": target,
                "agent": place.get("agent"),
                "harness": place.get("harness"),
                "cloned_from": {"uid": place["session_uid"], "at_turn": copied.get("at_turn")},
            }
        )
    row = {
        "execution_id": new_execution,
        "team": dict(session["team"]),
        "owner": dict(session.get("owner") or {"uid": ""}),
        "state": "paused",
        "title": title or str(session.get("title") or ""),
        "seats": seats,
        "lineage": {"forked_from": {"execution_id": execution_id}, "imported_from": None},
        "started_at": _now(),
    }
    TeamSessionStore().put(row)
    if emit(row, output):
        return
    console.print(
        f"Cloned {execution_id} as {new_execution} (paused): "
        + ", ".join(f"{one['id']} → {one['session_uid'] or 'no session'}" for one in seats)
        + f". Resume it: agent-teams sessions resume {new_execution}",
        markup=False,
        highlight=False,
    )


def described_row(row: Mapping[str, Any], app_id: str) -> dict[str, Any]:
    """A session row as the record lists it, in the shape the file format exports —
    `describe()`'s: `opened_by` as `{kind, uid}`, `app` as `{id, version}`."""
    opened_by = row.get("opened_by")
    if not isinstance(opened_by, Mapping):
        uid = str(opened_by or row.get("owner_uid") or "")
        opened_by = {"kind": "person" if uid else "nobody", "uid": uid}
    app = row.get("app") if isinstance(row.get("app"), Mapping) else {}
    version = int(row.get("version") or app.get("version") or 0)
    return {
        **row,
        "opened_by": opened_by,
        "app": {"id": str(app.get("id") or "") or app_id, "version": version},
        "instance": dict(
            row.get("instance") or {"app_uid": str(row.get("app_uid") or ""), "version": version}
        ),
    }


def team_line(session: Mapping[str, Any], *, exported_by: str, redactions: int) -> dict[str, Any]:
    """The first line of a team session's file (``record_type: team_session``)."""
    return {
        "record_type": "team_session",
        "format": FORMAT,
        "execution_id": str(session["execution_id"]),
        "team": {
            "id": str(session["team"]["id"]),
            "version": str(session["team"].get("version") or "0"),
        },
        "owner": {"uid": str((session.get("owner") or {}).get("uid") or "")},
        "state": check_state(str(session["state"])),
        "seats": [
            {
                "id": str(one["id"]),
                "session_uid": str(one["session_uid"]),
                "agent": str(one.get("agent") or ""),
                "harness": one.get("harness") or None,
            }
            for one in session["seats"]
            if one.get("session_uid")
        ],
        "lineage": dict(session.get("lineage") or {"forked_from": None, "imported_from": None}),
        "exported_at": _now(),
        "exported_by": {"uid": exported_by},
        "redactions": int(redactions),
    }


@sessions_app.command("export")
@orchestration_command
def export(
    execution_id: str = typer.Argument(..., help="The run to export: its root execution."),
    out: Optional[Path] = typer.Option(
        None, "--out", help="The file; `<execution>.team-session.jsonl` beside you otherwise."
    ),
    local: bool = local_option(),
    account: Optional[str] = account_option(),
    output: OutputFormat = output_option(),
) -> None:
    """The team line, then each seat's session: a datalayer-session/1 file, redacted."""
    session, record = _session(local, execution_id, account)
    without = [one["id"] for one in session["seats"] if not one.get("session_uid")]
    if without:
        raise OrchestrationCommandError(
            f"Team session {execution_id} cannot be exported: its seat"
            + ("s " if len(without) > 1 else " ")
            + ", ".join(without)
            + " kept no session."
        )
    fmt = _format()
    exporter = str((session.get("owner") or {}).get("uid") or "")
    lines: list[str] = []
    redactions = 0
    turns = 0
    app_id = f"team-{session['team']['id']}"
    for place in session["seats"]:
        read = record.session(str(place["session_uid"]))
        exported = fmt.export(
            described_row(read["session"], app_id), read["entries"], exported_by=exporter
        )
        redactions += exported.redactions
        turns += exported.turns
        lines.extend(exported.lines)
    head = json.dumps(
        team_line(session, exported_by=exporter, redactions=redactions),
        ensure_ascii=False,
        default=str,
    )
    text = "\n".join([head, *lines]) + "\n"
    digest = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    path = out or Path(f"{execution_id}.team-session.jsonl")
    path.write_text(text, encoding="utf-8")
    said = {
        "file": str(path),
        "seats": len(session["seats"]),
        "turns": turns,
        "entries": len(lines) - len(session["seats"]),
        "redactions": redactions,
        "digest": digest,
    }
    if emit(said, output):
        return
    console.print(
        f"Exported {len(session['seats'])} seats, {turns} turns, {said['entries']} entries, {redactions} redactions → {path} ({digest}).",
        markup=False,
        highlight=False,
    )


def _segments(  # noqa: C901
    lines: Sequence[str],
) -> tuple[dict[str, Any], list[tuple[dict[str, Any], list[str]]]]:
    """The team line, and each seat's session line with its entry lines."""
    try:
        from agentspecs.sessions import SessionFileError, parse_line
    except ImportError as error:
        raise OrchestrationCommandError(
            "A session file is read against agentspecs' schema: `pip install agentspecs`."
        ) from error
    parsed: list[tuple[Any, str]] = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError:
            raise OrchestrationCommandError(f"Line {number} of the file is not JSON.") from None
        try:
            parsed.append((parse_line(value, line=number), line))
        except SessionFileError as refused:
            raise OrchestrationCommandError(str(refused)) from None
    if not parsed:
        raise OrchestrationCommandError(
            "The file is empty: a team session file's first line is its team line."
        )
    head, _ = parsed[0]
    if head.record_type == "session":
        raise OrchestrationCommandError(
            "The file's first line is one session, not a team session: import it with `loop sessions import`."
        )
    if head.record_type != "team_session":
        raise OrchestrationCommandError(
            "A team session file's first line is a team line that says its format, datalayer-session/1; this file's first line does not."
        )
    segments: list[tuple[dict[str, Any], list[str]]] = []
    for item, line in parsed[1:]:
        if item.record_type == "session":
            segments.append((item.model_dump(mode="json"), [line]))
        elif item.record_type == "entry":
            if not segments:
                raise OrchestrationCommandError(
                    "An entry line follows the team line before any seat's session line."
                )
            segments[-1][1].append(line)
        else:
            raise OrchestrationCommandError(
                "A team session file holds one team line, first; this one holds another."
            )
    return head.model_dump(mode="json"), segments


@sessions_app.command("import")
@orchestration_command
def import_session(
    file: Path = typer.Argument(
        ..., help="A team session file, as `agent-teams sessions export` writes one."
    ),
    local: bool = local_option(),
    account: Optional[str] = account_option(),
    output: OutputFormat = output_option(),
) -> None:
    """A new team session, paused, whose seats are the file's sessions imported as new ones."""
    if not file.is_file():
        raise OrchestrationCommandError(f"There is no file {file}.")
    text = file.read_text(encoding="utf-8")
    head, segments = _segments(text.splitlines())
    team = _catalogue_team(str(head["team"]["id"]))
    known = {member.id for member in team.agents}
    for seat in head["seats"]:
        if seat["id"] not in known:
            raise OrchestrationCommandError(
                f"The file's seat {seat['id']!r} is not a seat of team {team.id}: one of "
                + ", ".join(sorted(known))
                + "."
            )
    by_uid = {str(session["uid"]): (session, lines) for session, lines in segments}
    missing = [seat["id"] for seat in head["seats"] if str(seat["session_uid"]) not in by_uid]
    if missing:
        raise OrchestrationCommandError(
            "The team line names seats whose session is not in the file: "
            + ", ".join(missing)
            + "."
        )
    fmt = _format()
    record = _record(local, account)
    importer = ""
    digest = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    seats: list[dict[str, Any]] = []
    redactions = 0
    for seat in head["seats"]:
        session, lines = by_uid[str(seat["session_uid"])]
        try:
            imported = fmt.import_(lines)
        except Exception as refused:
            raise OrchestrationCommandError(str(getattr(refused, "reason", refused))) from None
        uid = new_uid()
        app_uid = f"team:{team.id}"
        opening = {
            "kind": "session",
            "summary": f"A session of {team.name}'s seat {seat['id']} imported",
            "payload": {
                "app": f"team-{team.id}",
                "state": "open",
                "agent": str(imported.session.get("agent") or seat["agent"]),
                "harness": imported.session.get("harness") or seat.get("harness"),
                "runs_on": {"kind": "team_run", "execution_id": "", "seat": seat["id"]},
                "title": str(imported.session.get("title") or ""),
                "archived": False,
                "lineage": {
                    "forked_from": None,
                    "imported_from": {
                        "digest": digest,
                        "exported_at": str(head["exported_at"]),
                        "exported_by": str(head["exported_by"]["uid"]),
                        "redactions": int(imported.redactions),
                    },
                },
            },
        }
        kept = [e for e in imported.entries if e["kind"] not in ("session", "paused", "ended")]
        trailer = [
            {
                "kind": "imported",
                "summary": f"Imported from a file exported {head['exported_at']}",
                "payload": {
                    "digest": digest,
                    "exported_at": str(head["exported_at"]),
                    "exported_by": str(head["exported_by"]["uid"]),
                    "redactions": int(imported.redactions),
                    "file_version": FORMAT,
                    "current_version": FORMAT,
                    "source_uid": str(session["uid"]),
                },
            },
            {
                "kind": "paused",
                "summary": "Imported, paused until its run resumes it",
                "payload": {"state": "paused", "runs_on": {}},
            },
        ]
        record.write(uid, app_uid, [opening, *kept, *trailer], opened_by=importer)
        redactions += int(imported.redactions)
        seats.append(
            {
                "id": seat["id"],
                "session_uid": uid,
                "agent": opening["payload"]["agent"],
                "harness": opening["payload"]["harness"],
                "imported_from": str(session["uid"]),
            }
        )
    new_execution = f"team-session-{uuid.uuid4().hex[:12]}"
    row = {
        "execution_id": new_execution,
        "team": {"id": team.id, "version": str(head["team"].get("version") or team.version or "0")},
        "owner": {"uid": importer},
        "state": "paused",
        "title": "",
        "seats": seats,
        "lineage": {
            "forked_from": None,
            "imported_from": {
                "digest": digest,
                "exported_at": str(head["exported_at"]),
                "exported_by": str(head["exported_by"]["uid"]),
                "redactions": redactions,
            },
        },
        "started_at": _now(),
    }
    TeamSessionStore().put(row)
    if emit(row, output):
        return
    console.print(
        f"Imported as {new_execution} (paused, {len(seats)} seats, {redactions} redactions). "
        f"Resume it: agent-teams sessions resume {new_execution}",
        markup=False,
        highlight=False,
    )


@sessions_app.command("end")
@orchestration_command
def end(
    execution_id: str = typer.Argument(..., help="The run to end: its root execution."),
    local: bool = local_option(),
    account: Optional[str] = account_option(),
    output: OutputFormat = output_option(),
) -> None:
    """End a run for good: its seats' sessions end with it."""
    session, record = _session(local, execution_id, account)
    if session["state"] == "ended":
        raise OrchestrationCommandError(f"Team session {execution_id} ended already.")
    if local or not session.get("execution_state"):
        store = TeamSessionStore()
        row = store.get(execution_id) or dict(session)
        row["state"] = "ended"
        store.put(row)
        if local:
            files = FileSessionStore()
            for seat in session["seats"]:
                if seat.get("session_uid") and seat.get("state") != "ended":
                    files.append(
                        str(seat["session_uid"]),
                        [
                            {
                                "kind": "ended",
                                "summary": "Ended with its run",
                                "payload": {"state": "ended", "by": "run"},
                            }
                        ],
                    )
        if emit({"execution_id": execution_id, "state": "ended"}, output):
            return
        console.print(
            f"Ended {execution_id} and its seats' sessions.", markup=False, highlight=False
        )
        return
    from datalayer_core.cli.commands.executions import keyed, show_receipt

    command = ExecutionsTerminate(
        idempotency_key=_PENDING_KEY,
        execution_id=execution_id,
        reason="Ended by its owner.",
        release_worker=True,
    )
    receipt = call(
        lambda: record.client.terminate_execution(keyed(command, None), account_uid=account)
    )
    show_receipt(
        receipt,
        output,
        delivered="Received, and the run is being ended: its seats' sessions end with it.",
        undelivered="Received; it has not been ended yet.",
    )


def register(app: typer.Typer) -> None:
    """Add the `sessions` group to the ``agent-teams`` application."""
    app.add_typer(sessions_app)


__all__ = [
    "EXECUTION_TO_SESSION",
    "LocalRecord",
    "PlatformRecord",
    "local_team_session",
    "platform_team_session",
    "register",
    "seat_row",
    "session_state_of",
    "sessions_app",
    "team_line",
    "team_of",
]
