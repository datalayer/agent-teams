# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""A self-hosted run whose process is gone is ended, and its seats' sessions with it.

A paused session is always resumable: a run its file says is ``open`` while
nothing runs it — it crashed, the machine restarted — is not gone on with.
Its file says which process holds it (``held_by``) while it is open, and the
store ends it, and each seat's session, as the file is read. The same rule
as the platform's record and agent-runtimes' files keep.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_teams.sessions.store import (
    SESSIONS_PATH_VARIABLE,
    TEAMS_PATH_VARIABLE,
    FileSessionStore,
    TeamSessionStore,
    holder,
    process_gone,
    this_machine,
)

GONE = {"pid": 4_000_000, "host": this_machine()}


@pytest.fixture
def stores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TeamSessionStore, FileSessionStore]:
    monkeypatch.setenv(SESSIONS_PATH_VARIABLE, str(tmp_path / "sessions"))
    monkeypatch.setenv(TEAMS_PATH_VARIABLE, str(tmp_path / "teams"))
    return TeamSessionStore(), FileSessionStore()


def _run(
    teams: TeamSessionStore, files: FileSessionStore, execution_id: str, **row: object
) -> None:
    uid = f"seat-of-{execution_id}"
    files.append(
        uid,
        [{"kind": "session", "payload": {"state": "open", "agent": "coder"}}],
        app_uid="team:crew",
    )
    teams.put(
        {
            "execution_id": execution_id,
            "team": {"id": "crew", "version": "1"},
            "state": "open",
            "seats": [{"id": "coder", "session_uid": uid}],
            "started_at": "2026-11-12T10:00:00+00:00",
            **row,
        }
    )


def test_the_holder_is_this_process_and_names_the_machine_by_a_digest() -> None:
    import socket

    assert holder() == {"pid": os.getpid(), "host": this_machine()}
    assert socket.gethostname() not in str(holder())
    assert process_gone(holder()) is False and process_gone(GONE) is True
    assert process_gone(None) is None and process_gone({"pid": 1, "host": "elsewhere"}) is None


def test_a_run_whose_process_is_gone_is_ended_with_its_seats_as_it_is_read(stores) -> None:
    teams, files = stores
    _run(teams, files, "exec_crashed", held_by=GONE)
    _run(teams, files, "exec_running", held_by=holder())
    _run(teams, files, "exec_unknown")
    states = {row["execution_id"]: row["state"] for row in teams.list()}
    assert states == {"exec_crashed": "ended", "exec_running": "open", "exec_unknown": "open"}
    crashed = teams.get("exec_crashed")
    assert crashed["state"] == "ended" and "held_by" not in crashed
    assert (
        crashed["ended_why"]
        == "Its team run exec_crashed stopped without ending: its process is gone."
    )
    # Its seat's session ended with it, by the run; the others' are as they were.
    last = files.entries("seat-of-exec_crashed")[-1]
    assert last["kind"] == "ended" and last["payload"]["by"] == "run"
    assert files.describe("seat-of-exec_crashed")["state"] == "ended"
    assert files.describe("seat-of-exec_running")["state"] == "open"
    # Read again, nothing more is written.
    assert (
        len(files.entries("seat-of-exec_crashed")) == 2
        and teams.get("exec_crashed")["state"] == "ended"
    )
