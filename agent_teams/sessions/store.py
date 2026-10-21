# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Where a self-hosted team keeps its sessions: files (MH-23).

A seat's session is kept as the record keeps any session — one folder per
session, ``<root>/<uid>/entries.jsonl``, one entry a line in the record's
line shape — under the same root agent-runtimes' file session store reads
(``~/.datalayer/sessions``, or ``AGENT_RUNTIMES_SESSIONS_PATH``), so
``loop sessions list`` lists a seat's session beside any other. A harness
seat's own session — the ACP session id and the workspace, what a
``session/load`` needs — sits beside the entries as ``harness_session.json``.

A team session — one run of a team — is kept beside them as one file,
``<teams>/<execution_id>.json``: the team, the owner, the state, the seats in
order with each one's session uid, the lineage. On the platform the run is
the root execution and the control plane carries the uids; here the file is
the run.

Nothing is read as a default: a session folder without a ``session`` entry
is not a session that can be described, and a state word that is none of
open, paused or ended is refused in a sentence.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: The root agent-runtimes' file session store reads too.
SESSIONS_PATH_VARIABLE = "AGENT_RUNTIMES_SESSIONS_PATH"
DEFAULT_SESSIONS_PATH = Path.home() / ".datalayer" / "sessions"
#: Where the team sessions of a self-hosted server are kept.
TEAMS_PATH_VARIABLE = "AGENT_TEAMS_SESSIONS_PATH"
DEFAULT_TEAMS_PATH = Path.home() / ".datalayer" / "teams" / "sessions"
ENTRIES_FILE = "entries.jsonl"
HARNESS_FILE = "harness_session.json"

#: A session's states (decision 14): any other word is refused.
SESSION_STATES = ("open", "paused", "ended")


class SessionRefused(Exception):
    """An operation on a session refused: its status and its reason, in a sentence."""

    def __init__(self, status: int, reason: str):
        self.status = status
        self.reason = reason
        super().__init__(reason)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_uid() -> str:
    """A session uid: 8 to 128 letters, digits, hyphens or underscores."""
    return uuid.uuid4().hex


def check_state(word: str) -> str:
    """The state word as it is, or :class:`SessionRefused` (422) naming the three."""
    if word not in SESSION_STATES:
        raise SessionRefused(
            422, f"There is no session state {word!r}: one of {', '.join(SESSION_STATES)}."
        )
    return word


def describe_session(uid: str, entries: Sequence[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """A session as the record says it (§2 of the session model), from its
    entries in order — the row ``GET /sessions`` lists: ``None`` when the
    entries hold no ``session`` entry (never a default).

    Raises
    ------
    SessionRefused
        When an entry says a state that is none (``stopped`` included).
    """
    opened = next((e for e in entries if e.get("kind") == "session"), None)
    if opened is None:
        return None
    payload = dict(opened.get("payload") or {})
    lineage = dict(payload.get("lineage") or {"forked_from": None, "imported_from": None})
    described: Dict[str, Any] = {
        "uid": uid,
        "agent": str(payload.get("agent") or ""),
        "harness": payload.get("harness") or None,
        "app": {"id": str(payload.get("app") or ""), "version": int(opened.get("version") or 0)},
        "opened_by": {
            "kind": "person" if opened.get("opened_by") else "nobody",
            "uid": str(opened.get("opened_by") or ""),
        },
        "state": "open",
        "turn": "idle",
        "held": False,
        "title": str(payload.get("title") or "")[:200],
        "archived": bool(payload.get("archived", False)),
        "lineage": lineage,
        "runs_on": dict(payload.get("runs_on") or {}),
        "turns": 0,
        "started_at": str(opened.get("at") or ""),
    }
    for entry in entries:
        kind, payload = entry.get("kind"), dict(entry.get("payload") or {})
        if kind in ("session", "paused", "ended"):
            described["state"] = check_state(str(payload.get("state") or "open"))
            if kind == "session" and payload.get("runs_on"):
                described["runs_on"] = dict(payload["runs_on"])
        elif kind == "turn":
            described["turns"] += 1
        elif kind == "swapped":
            described["agent"] = str(payload.get("agent") or payload.get("to") or described["agent"])
            described["harness"] = payload.get("harness") or payload.get("to_harness")
        elif kind == "titled":
            described["title"] = str(payload.get("title") or "")[:200]
            described["archived"] = bool(payload.get("archived", described["archived"]))
        elif kind == "cloned" and payload.get("from"):
            described["lineage"] = {
                **described["lineage"],
                "forked_from": {"uid": str(payload["from"]), "at_turn": int(payload.get("at_turn") or 0)},
            }
        elif kind == "imported":
            described["lineage"] = {
                **described["lineage"],
                "imported_from": {
                    "digest": str(payload.get("digest") or ""),
                    "exported_at": str(payload.get("exported_at") or ""),
                    "exported_by": str(payload.get("exported_by") or ""),
                    "redactions": int(payload.get("redactions") or 0),
                },
            }
    if described["state"] == "ended":
        described["runs_on"] = None
    return described


def entries_up_to_turn(entries: Sequence[Mapping[str, Any]], up_to_turn: Optional[int]) -> List[Dict[str, Any]]:
    """The entries up to and including the ``up_to_turn``-th turn; all when none is named.

    Raises
    ------
    SessionRefused
        A turn the session does not have (422).
    """
    if up_to_turn is None:
        return [dict(e) for e in entries]
    turns = sum(1 for e in entries if e.get("kind") == "turn")
    if up_to_turn < 1 or up_to_turn > turns:
        raise SessionRefused(
            422, f"The session has {turns} turns; there is no turn {up_to_turn} to cut it at."
        )
    kept: List[Dict[str, Any]] = []
    seen = 0
    for entry in entries:
        kept.append(dict(entry))
        if entry.get("kind") == "turn":
            seen += 1
            if seen == up_to_turn:
                break
    return kept


@dataclass
class FileSessionStore:
    """Sessions in files, in the record's line shape: ``<root>/<uid>/entries.jsonl``."""

    root: Path = field(
        default_factory=lambda: Path(
            os.environ.get(SESSIONS_PATH_VARIABLE, "").strip() or DEFAULT_SESSIONS_PATH
        )
    )

    def _folder(self, uid: str) -> Path:
        return self.root / uid

    def exists(self, uid: str) -> bool:
        return (self._folder(uid) / ENTRIES_FILE).is_file()

    def entries(self, uid: str) -> List[Dict[str, Any]]:
        path = self._folder(uid) / ENTRIES_FILE
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def describe(self, uid: str) -> Optional[Dict[str, Any]]:
        return describe_session(uid, self.entries(uid))

    def append(
        self,
        uid: str,
        entries: Sequence[Mapping[str, Any]],
        *,
        app_uid: str = "",
        opened_by: str = "",
        version: int = 0,
    ) -> None:
        """Add entries to a session's file, each in the record's line shape."""
        path = self._folder(uid) / ENTRIES_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        at = now()
        with path.open("a") as out:
            for entry in entries:
                line = {
                    "app_uid": str(app_uid or entry.get("app_uid") or ""),
                    "deployment_uid": "",
                    "session_uid": uid,
                    "version": int(entry.get("version") or version or 0),
                    "purpose": "",
                    "opened_by": str(opened_by or entry.get("opened_by") or ""),
                    "kind": str(entry.get("kind") or ""),
                    "summary": str(entry.get("summary") or ""),
                    "payload": dict(entry.get("payload") or {}),
                    "at": str(entry.get("at") or at),
                }
                out.write(json.dumps(line, default=str) + "\n")

    def copy(self, uid: str, to_uid: str, *, up_to_turn: Optional[int] = None) -> int:
        """Copy a session's entries under a new uid, up to a turn; how many went."""
        if self.exists(to_uid):
            raise SessionRefused(409, f"There is a session {to_uid} already: a clone takes a new uid.")
        kept = entries_up_to_turn(self.entries(uid), up_to_turn)
        self.append(to_uid, [{**e, "session_uid": to_uid} for e in kept])
        return len(kept)

    def harness_session(self, uid: str) -> Optional[Dict[str, Any]]:
        """A harness seat's own session beside the entries, or ``None``."""
        path = self._folder(uid) / HARNESS_FILE
        return json.loads(path.read_text()) if path.is_file() else None

    def keep_harness_session(self, uid: str, row: Mapping[str, Any]) -> None:
        path = self._folder(uid) / HARNESS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(row), default=str))


@dataclass
class TeamSessionStore:
    """Team sessions in files: ``<root>/<execution_id>.json``, one run each."""

    root: Path = field(
        default_factory=lambda: Path(
            os.environ.get(TEAMS_PATH_VARIABLE, "").strip() or DEFAULT_TEAMS_PATH
        )
    )

    def _file(self, execution_id: str) -> Path:
        return self.root / f"{execution_id}.json"

    def get(self, execution_id: str) -> Optional[Dict[str, Any]]:
        path = self._file(execution_id)
        return json.loads(path.read_text()) if path.is_file() else None

    def put(self, row: Mapping[str, Any]) -> None:
        check_state(str(row.get("state") or ""))
        path = self._file(str(row["execution_id"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(row), default=str, indent=2))

    def list(self, *, team: str = "") -> List[Dict[str, Any]]:
        """Every team session, newest first; one team's when named."""
        if not self.root.is_dir():
            return []
        rows = [json.loads(p.read_text()) for p in self.root.glob("*.json")]
        rows = [r for r in rows if not team or (r.get("team") or {}).get("id") == team]
        return sorted(rows, key=lambda r: str(r.get("started_at") or ""), reverse=True)


__all__ = [
    "DEFAULT_SESSIONS_PATH",
    "DEFAULT_TEAMS_PATH",
    "ENTRIES_FILE",
    "HARNESS_FILE",
    "SESSIONS_PATH_VARIABLE",
    "SESSION_STATES",
    "TEAMS_PATH_VARIABLE",
    "FileSessionStore",
    "SessionRefused",
    "TeamSessionStore",
    "check_state",
    "describe_session",
    "entries_up_to_turn",
    "new_uid",
    "now",
]
