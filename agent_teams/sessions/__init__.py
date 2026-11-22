# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""A team's sessions: the run as a session, one session per seat (MH-23).

The stores are the local ones — the record's line shape in files, under the
root agent-runtimes' file session store reads — and :class:`SeatSessions`
is what an orchestration contribution owns for a run.
"""

from .seats import SESSION_CONTEXT, SeatSession, SeatSessions, first_line, seat_agent
from .store import (
    DEFAULT_SESSIONS_PATH,
    DEFAULT_TEAMS_PATH,
    SESSION_STATES,
    FileSessionStore,
    SessionRefused,
    TeamSessionStore,
    check_state,
    describe_session,
    entries_up_to_turn,
    new_uid,
)

__all__ = [
    "DEFAULT_SESSIONS_PATH",
    "DEFAULT_TEAMS_PATH",
    "SESSION_CONTEXT",
    "SESSION_STATES",
    "FileSessionStore",
    "SeatSession",
    "SeatSessions",
    "SessionRefused",
    "TeamSessionStore",
    "check_state",
    "describe_session",
    "entries_up_to_turn",
    "first_line",
    "new_uid",
    "seat_agent",
]
