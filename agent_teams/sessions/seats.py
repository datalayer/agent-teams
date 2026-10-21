# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""The seats' sessions of one team run, owned by its orchestration (MH-F07, MH-23).

A team session is one run of a team; it owns one session per seat, opened at
the seat's first task and kept until the run ends. The orchestration
contribution of the run's mode builds a :class:`SeatSessions` for the run
and wraps each member's run function with it: before a task, the seat's
session is opened (its first task) or resumed (the next); the task is a
turn of it, kept as the record keeps a turn; the run's end ends them all.

Every operation fires the team's hooks (decision S): ``session-open`` at a
seat's first task, ``session-resume`` at its next, ``session-end`` when the
run ends — the same events the runtime's ``session.hook`` point has, on the
team's own :class:`~agent_teams.hooks.HookRegistry`; a hook that disallows
refuses the operation with its reason, in a sentence. ``post-team-stop``
ends the seats' sessions, so a team stopped any way ends them.

On the platform a seat's session is opened in the seat's worker and the
control plane carries its uid (decision T); here, the local file store is
the record and the run is its file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence

from ..hooks import HookEvent, HookRegistry
from ..types import AgentMemberConfig
from .store import FileSessionStore, SessionRefused, TeamSessionStore, new_uid, now

logger = logging.getLogger(__name__)

#: What a run function is: the prompt and the task's context, the answer.
RunFn = Callable[[str, Dict[str, Any]], Awaitable[str]]

#: What a seat's task is told about its session, under ``context["session"]``.
SESSION_CONTEXT = "session"


def seat_agent(member: AgentMemberConfig) -> str:
    """The agent a seat's session is bound to: its agentspec, else its harness
    (``harness:<id>``), else its model (``model:<id>``)."""
    if member.harness:
        return f"harness:{member.harness}"
    if member.agent_spec_id:
        return member.agent_spec_id
    return f"model:{member.model}"


def first_line(text: str, limit: int = 60) -> str:
    """A title from a text: its first line, cut at ``limit``."""
    line = next((one.strip() for one in text.splitlines() if one.strip()), "")
    return line[:limit]


@dataclass
class SeatSession:
    """One seat's session in a run."""

    seat: str
    uid: str
    agent: str
    harness: Optional[str]
    execution_id: str
    turns: int = 0
    state: str = "open"

    def runs_on(self) -> Dict[str, Any]:
        return {"kind": "team_run", "execution_id": self.execution_id, "seat": self.seat}

    def row(self) -> Dict[str, Any]:
        return {"id": self.seat, "session_uid": self.uid, "agent": self.agent, "harness": self.harness}


@dataclass
class SeatSessions:
    """The seats' sessions of one run, kept in the local stores."""

    execution_id: str
    team_id: str
    team_version: str
    owner_uid: str
    members: Sequence[AgentMemberConfig]
    hooks: Optional[HookRegistry] = None
    store: FileSessionStore = field(default_factory=FileSessionStore)
    teams: TeamSessionStore = field(default_factory=TeamSessionStore)
    #: Seat uids a resumed, cloned or imported team session comes with: the
    #: seat's first task resumes that session instead of opening a new one.
    given: Mapping[str, str] = field(default_factory=dict)
    state: str = "open"
    lineage: Dict[str, Any] = field(
        default_factory=lambda: {"forked_from": None, "imported_from": None}
    )
    title: str = ""
    _seats: Dict[str, SeatSession] = field(default_factory=dict, init=False)
    _started_at: str = field(default_factory=now, init=False)

    # -- the team session's row -------------------------------------------------

    @property
    def app_uid(self) -> str:
        return f"team:{self.team_id}"

    def seat(self, seat_id: str) -> Optional[SeatSession]:
        return self._seats.get(seat_id)

    def seats(self) -> List[SeatSession]:
        return list(self._seats.values())

    def row(self) -> Dict[str, Any]:
        """The team session as a row: the run, its seats, its state."""
        return {
            "execution_id": self.execution_id,
            "team": {"id": self.team_id, "version": self.team_version},
            "owner": {"uid": self.owner_uid},
            "state": self.state,
            "title": self.title,
            "seats": [one.row() for one in self._seats.values()],
            "lineage": dict(self.lineage),
            "started_at": self._started_at,
        }

    def write_row(self) -> None:
        self.teams.put(self.row())

    def attach(self) -> None:
        """Register on the team's hooks: ``post-team-stop`` ends the seats' sessions."""
        if self.hooks is None:
            return
        execution_id = self.execution_id

        async def ends_the_seats(event: Any, **facts: Any) -> None:
            if facts.get("execution_id", execution_id) == execution_id:
                await self.end()

        self.hooks.register(
            HookEvent.POST_TEAM_STOP, ends_the_seats, name=f"sessions:{execution_id}"
        )
        self.write_row()

    async def fire(self, event: HookEvent, **facts: Any) -> None:
        """Run the team's hooks of a session event; a hook that disallows refuses it."""
        if self.hooks is None:
            return
        result = await self.hooks.run(event, execution_id=self.execution_id, **facts)
        if not result.allow:
            raise SessionRefused(409, result.reason or f"A hook refused {event.value}.")

    # -- the seats ---------------------------------------------------------------

    def _member(self, seat_id: str) -> AgentMemberConfig:
        member = next((one for one in self.members if one.id == seat_id), None)
        if member is None:
            raise SessionRefused(
                422,
                f"Team {self.team_id} has no seat {seat_id!r}: one of "
                + ", ".join(one.id for one in self.members)
                + ".",
            )
        return member

    async def open_or_resume(self, member: AgentMemberConfig) -> SeatSession:
        """The seat's session: opened at its first task, resumed at the next."""
        if self.state != "open":
            raise SessionRefused(
                409,
                f"Team session {self.execution_id} is {self.state}: "
                + ("resume it before going on." if self.state == "paused" else "an ended run is not resumed — clone it to go on from it."),
            )
        held = self._seats.get(member.id)
        if held is not None:
            await self.fire(HookEvent.SESSION_RESUME, seat=member.id, session_uid=held.uid)
            return held
        uid = self.given.get(member.id) or ""
        if uid and self.store.exists(uid):
            seat = self._resumed(member, uid)
            await self.fire(HookEvent.SESSION_RESUME, seat=member.id, session_uid=uid)
        else:
            seat = self._opened(member, uid or new_uid())
            await self.fire(HookEvent.SESSION_OPEN, seat=member.id, session_uid=seat.uid)
        self._seats[member.id] = seat
        self.write_row()
        return seat

    def _facts(self, seat: SeatSession, member: AgentMemberConfig) -> Dict[str, Any]:
        return {
            "app": self.app_uid,
            "state": "open",
            "agent": seat.agent,
            "harness": seat.harness,
            "runs_on": seat.runs_on(),
            "title": first_line(member.goal or member.name),
            "archived": False,
            "lineage": {"forked_from": None, "imported_from": None},
        }

    def _opened(self, member: AgentMemberConfig, uid: str) -> SeatSession:
        seat = SeatSession(
            seat=member.id,
            uid=uid,
            agent=seat_agent(member),
            harness=member.harness or None,
            execution_id=self.execution_id,
        )
        self.store.append(
            uid,
            [
                {
                    "kind": "session",
                    "summary": f"A session of {self.team_id}'s seat {member.id} started",
                    "payload": self._facts(seat, member),
                }
            ],
            app_uid=self.app_uid,
            opened_by=self.owner_uid,
        )
        return seat

    def _resumed(self, member: AgentMemberConfig, uid: str) -> SeatSession:
        described = self.store.describe(uid)
        if described is None:
            raise SessionRefused(
                422, f"Session {uid} has no session entry in its record: it cannot be resumed."
            )
        if described["state"] == "ended":
            raise SessionRefused(
                409,
                f"Session {uid} ended; an ended session is not resumed — clone it to go on from it.",
            )
        seat = SeatSession(
            seat=member.id,
            uid=uid,
            agent=str(described["agent"] or seat_agent(member)),
            harness=described.get("harness") or member.harness or None,
            execution_id=self.execution_id,
            turns=int(described["turns"]),
        )
        self.store.append(
            uid,
            [
                {
                    "kind": "session",
                    "summary": f"A session of {self.team_id}'s seat {member.id} resumed",
                    "payload": {**self._facts(seat, member), "resumed": True, "title": described["title"]},
                }
            ],
            app_uid=self.app_uid,
            opened_by=self.owner_uid,
        )
        return seat

    def turn(self, member: AgentMemberConfig, asked: str, answered: str) -> None:
        """Keep a task as a turn of the seat's session."""
        seat = self._seats[member.id]
        seat.turns += 1
        self.store.append(
            seat.uid,
            [
                {
                    "kind": "turn",
                    "summary": asked.strip()[:2000],
                    "payload": {"asked": asked.strip()[:4000], "answered": answered.strip()[:4000]},
                }
            ],
            app_uid=self.app_uid,
            opened_by=self.owner_uid,
        )

    def wrap(self, member: AgentMemberConfig, run: RunFn) -> RunFn:
        """The member's run function as a turn of its session."""

        async def as_a_turn(prompt: str, context: Dict[str, Any]) -> str:
            seat = await self.open_or_resume(member)
            told = {
                **(context or {}),
                SESSION_CONTEXT: {
                    "uid": seat.uid,
                    "execution_id": self.execution_id,
                    "seat": seat.seat,
                    "turns": seat.turns,
                },
            }
            answer = await run(prompt, told)
            self.turn(member, prompt, answer)
            return answer

        return as_a_turn

    # -- the run's end ----------------------------------------------------------------

    async def pause(self) -> None:
        """Pause the run: every seat's session gets its ``paused`` entry."""
        if self.state != "open":
            raise SessionRefused(409, f"Team session {self.execution_id} is {self.state}, not open.")
        for seat in self._seats.values():
            seat.state = "paused"
            self.store.append(
                seat.uid,
                [{"kind": "paused", "summary": "Paused with its run", "payload": {"state": "paused"}}],
                app_uid=self.app_uid,
                opened_by=self.owner_uid,
            )
        self.state = "paused"
        self.write_row()

    async def resume(self) -> None:
        """Let a paused run go on: the seats go on from where they were."""
        if self.state == "ended":
            raise SessionRefused(
                409,
                f"Team session {self.execution_id} ended; an ended run is not resumed — clone it to go on from it.",
            )
        if self.state != "paused":
            raise SessionRefused(409, f"Team session {self.execution_id} is open, not paused.")
        self.state = "open"
        for seat in self._seats.values():
            seat.state = "open"
        self.write_row()

    async def end(self) -> None:
        """End the run: every seat's session ends with it, once."""
        if self.state == "ended":
            return
        await self.fire(HookEvent.SESSION_END, seats={s.seat: s.uid for s in self._seats.values()})
        for seat in self._seats.values():
            seat.state = "ended"
            self.store.append(
                seat.uid,
                [{"kind": "ended", "summary": "Ended with its run", "payload": {"state": "ended", "by": "run"}}],
                app_uid=self.app_uid,
                opened_by=self.owner_uid,
            )
        self.state = "ended"
        self.write_row()


__all__ = ["SESSION_CONTEXT", "RunFn", "SeatSession", "SeatSessions", "first_line", "seat_agent"]
