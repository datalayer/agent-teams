# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""A team member that is a coding agent, behind the Shield (SHIELD SH-45, MH-23).

A member naming a ``harness`` (Claude Code, Codex, any agent of the Agent
Client Protocol in the catalogue) is not a model with a prompt: it is the
agent program itself, run behind ``agent-shield run`` over stdio, the
Shield's policy deciding each file it reads or writes and each command it
runs in the member's workspace.

**One session for the run** (MH-23): the agent is launched and
``session/new`` opened in the member's workspace at its first task; the next
task is a prompt of the same session. When the run is resumed in another
process, the seat's own harness row — the ACP session id, kept beside the
seat's session entries — is loaded with ``session/load`` when the agent
advertises it, and the start-over is said in the answer when it does not,
never ``session/new`` in silence. The run's end ends the session with it.

Nobody watches a team run, so a permission the Shield would ask a person
for is refused here in a sentence (``reject``): the policy, not the runner,
is what lets an action through.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .plugins.points import MemberRunner, RunFn
from .sessions.seats import SESSION_CONTEXT
from .sessions.store import FileSessionStore
from .types import AgentMemberConfig


class HarnessRefusedError(RuntimeError):
    """What stops a harness member from running, in a sentence."""


def catalogue_command(harness_id: str) -> list[str]:
    """The launch command of a catalogue harness, from agent-shield's register."""
    try:
        from agent_shield.register import RegisterRefused, harness
    except ImportError as exc:  # pragma: no cover - the extra names it
        raise HarnessRefusedError(
            "a harness member runs behind the Shield: `pip install agent-teams[shield]`"
        ) from exc
    try:
        entry = harness(harness_id)
        return [entry.command, *entry.launch_args]
    except RegisterRefused as exc:
        raise HarnessRefusedError(str(exc)) from None


class _Member:
    """The client side of one session: keeps the agent's words, refuses what would need a person."""

    def __init__(self) -> None:
        self.words: list[str] = []
        self.refused: list[str] = []

    def begin(self) -> None:
        """A new turn: the words and the refusals of the last one let go."""
        self.words = []
        self.refused = []

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        if getattr(update, "session_update", None) != "agent_message_chunk":
            return
        content = getattr(update, "content", None)
        if getattr(content, "type", None) == "text":
            self.words.append(content.text)

    async def request_permission(
        self, options: Sequence[Any], session_id: str, tool_call: Any, **kwargs: Any
    ) -> Any:
        from acp.schema import (
            AllowedOutcome,
            DeniedOutcome,
            RequestPermissionResponse,
        )

        self.refused.append(str(getattr(tool_call, "title", "") or "an action"))
        reject = next((option for option in options if str(option.kind).startswith("reject")), None)
        if reject is None:
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        return RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", option_id=reject.option_id)
        )


@dataclass
class _Held:
    """What the runner holds for a member while its run lasts."""

    stack: contextlib.AsyncExitStack
    channel: Any
    seen: _Member
    acp_session_id: str
    #: The seat's session uid the ACP session serves.
    session_uid: str
    said_first: str = ""


@dataclass
class HarnessRunner:
    """Run functions for a team's harness members, each behind ``agent-shield run``.

    ``workspace`` is where the members work (each in a folder of its id);
    ``policy`` the Shield's policy file (``agent-shield policy init`` writes
    decision 5's default), else the Shield's own default; ``commands`` a
    launch command per harness id, else the catalogue's; ``shield`` how
    ``agent-shield`` is started (default: this Python's ``-m agent_shield.cli``);
    ``env`` what the agent is given beyond the trimmed environment (its key);
    ``store`` where a seat's harness row is kept beside its session.
    """

    workspace: Path | str
    policy: Path | str | None = None
    commands: Mapping[str, Sequence[str]] | None = None
    shield: Sequence[str] | None = None
    env: Mapping[str, str] | None = None
    store: FileSessionStore = field(default_factory=FileSessionStore)
    _held: dict[str, _Held] = field(default_factory=dict, init=False)

    @property
    def root(self) -> Path:
        return Path(self.workspace).resolve()

    def as_runner(self) -> MemberRunner:
        """The runner as the ``teams.member.runner`` point takes it."""
        return MemberRunner("harness", self.factory, self.end)

    def _argv(self, member: AgentMemberConfig) -> list[str]:
        if not member.harness:
            raise HarnessRefusedError(f"{member.name} is not a harness member")
        command = list(
            (self.commands or {}).get(member.harness) or catalogue_command(member.harness)
        )
        starter = list(self.shield) if self.shield else [sys.executable, "-m", "agent_shield.cli"]
        return [
            *starter,
            "run",
            *(["--policy", str(Path(self.policy).resolve())] if self.policy else []),
            "--",
            *command,
        ]

    async def _open(self, member: AgentMemberConfig, session_uid: str) -> _Held:
        """Launch the agent and open, or load, the seat's session."""
        from agent_shield.acp import StdioACPChannel

        argv = self._argv(member)
        place = self.root / member.id
        place.mkdir(parents=True, exist_ok=True)
        seen = _Member()
        stack = contextlib.AsyncExitStack()
        channel = await stack.enter_async_context(
            StdioACPChannel.launch(seen, argv[0], *argv[1:], cwd=place, env=dict(self.env or {}))
        )
        await channel.initialize()
        kept = self.store.harness_session(session_uid) if session_uid else None
        said = ""
        acp_session_id = ""
        if kept and kept.get("acp_session_id"):
            capabilities = channel.agent_capabilities
            if capabilities is not None and capabilities.load_session:
                await channel.load_session(str(kept["acp_session_id"]), place)
                acp_session_id = str(kept["acp_session_id"])
            else:
                said = (
                    f"{member.harness} advertises no loadSession, so its session {kept['acp_session_id']} "
                    "cannot be loaded: it starts over; the earlier turns of this seat were not replayed to it."
                )
        if not acp_session_id:
            answer = await channel.new_session(place)
            acp_session_id = answer.session_id
        if session_uid:
            self.store.keep_harness_session(
                session_uid,
                {
                    "acp_session_id": acp_session_id,
                    "harness": member.harness,
                    "agent": f"harness:{member.harness}",
                    "workspace": str(place),
                    "seat": member.id,
                },
            )
        return _Held(stack, channel, seen, acp_session_id, session_uid, said)

    def factory(self, member: AgentMemberConfig) -> RunFn:
        """The member's run function: one session for the run, a prompt per task."""
        if not member.harness:
            raise HarnessRefusedError(f"{member.name} is not a harness member")
        self._argv(member)  # refused now, in the register's sentence, not at the first task

        async def run(prompt: str, context: dict[str, Any]) -> str:
            from acp import text_block

            session_uid = str((context or {}).get(SESSION_CONTEXT, {}).get("uid") or "")
            held = self._held.get(member.id)
            if held is not None and session_uid and held.session_uid != session_uid:
                await self.end(member.id)
                held = None
            if held is None:
                held = await self._open(member, session_uid)
                self._held[member.id] = held
            held.seen.begin()
            turn = await held.channel.prompt(held.acp_session_id, [text_block(prompt)])
            answer = "".join(held.seen.words).strip()
            if held.said_first:
                answer = f"{held.said_first}\n\n{answer}" if answer else held.said_first
                held.said_first = ""
            if held.seen.refused:
                answer += (
                    "\n\nRefused, nobody being there to ask: " + "; ".join(held.seen.refused) + "."
                )
            if turn.stop_reason not in ("end_turn", "max_tokens", "max_turn_requests"):
                raise HarnessRefusedError(
                    f"{member.name}'s turn ended {turn.stop_reason}: {answer or 'nothing said'}"
                )
            return answer

        return run

    def session_of(self, member_id: str) -> str | None:
        """The ACP session id a member's run holds, or ``None``."""
        held = self._held.get(member_id)
        return held.acp_session_id if held is not None else None

    async def end(self, member_id: str) -> None:
        """End what the runner holds for a member: its session goes with its process."""
        held = self._held.pop(member_id, None)
        if held is not None:
            await held.stack.aclose()

    async def end_all(self) -> None:
        for member_id in list(self._held):
            await self.end(member_id)


def harness_run_factory(
    workspace: Path | str,
    *,
    policy: Path | str | None = None,
    commands: Mapping[str, Sequence[str]] | None = None,
    shield: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> HarnessRunner:
    """A :class:`HarnessRunner` — what ``teams_platform(harness=…)`` takes."""
    return HarnessRunner(workspace, policy=policy, commands=commands, shield=shield, env=env)


__all__ = [
    "HarnessRefusedError",
    "HarnessRunner",
    "RunFn",
    "catalogue_command",
    "harness_run_factory",
]
