# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""A team member that is a coding agent, behind the Shield (SHIELD SH-45).

A member naming a ``harness`` (Claude Code, Codex, any agent of the Agent
Client Protocol in the catalogue) is not a model with a prompt: it is the
agent program itself, run behind ``agent-shield run`` over stdio, the
Shield's policy deciding each file it reads or writes and each command it
runs in the member's workspace. Each task is one session: ``initialize``,
``session/new`` in the workspace, the task as the prompt, the agent's words
as the answer.

Nobody watches a team run, so a permission the Shield would ask a person
for is refused here in a sentence (``reject``): the policy, not the runner,
is what lets an action through.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Coroutine, Mapping, Sequence
from pathlib import Path
from typing import Any

from .types import AgentMemberConfig

#: What a run function is: the prompt and the task's context, the answer.
RunFn = Callable[[str, dict[str, Any]], Coroutine[Any, Any, str]]


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


def harness_run_factory(
    workspace: Path | str,
    *,
    policy: Path | str | None = None,
    commands: Mapping[str, Sequence[str]] | None = None,
    shield: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> Callable[[AgentMemberConfig], RunFn]:
    """Run functions for a team's harness members, each behind ``agent-shield run``.

    ``workspace`` is where the members work (each in a folder of its id);
    ``policy`` the Shield's policy file (``agent-shield policy init`` writes
    decision 5's default), else the Shield's own default; ``commands`` a
    launch command per harness id, else the catalogue's; ``shield`` how
    ``agent-shield`` is started (default: this Python's ``-m agent_shield.cli``);
    ``env`` what the agent is given beyond the trimmed environment (its key).
    """
    root = Path(workspace).resolve()
    starter = list(shield) if shield else [sys.executable, "-m", "agent_shield.cli"]

    def factory(member: AgentMemberConfig) -> RunFn:
        if not member.harness:
            raise HarnessRefusedError(f"{member.name} is not a harness member")
        command = list((commands or {}).get(member.harness) or catalogue_command(member.harness))
        place = root / member.id
        place.mkdir(parents=True, exist_ok=True)
        argv = [
            *starter,
            "run",
            *(["--policy", str(Path(policy).resolve())] if policy else []),
            "--",
            *command,
        ]

        async def run(prompt: str, context: dict[str, Any]) -> str:
            from acp import text_block
            from agent_shield.acp import StdioACPChannel

            seen = _Member()
            async with StdioACPChannel.launch(
                seen, argv[0], *argv[1:], cwd=place, env=dict(env or {})
            ) as channel:
                await channel.initialize()
                session = await channel.new_session(place)
                turn = await channel.prompt(session.session_id, [text_block(prompt)])
            answer = "".join(seen.words).strip()
            if seen.refused:
                answer += "\n\nRefused, nobody being there to ask: " + "; ".join(seen.refused) + "."
            if turn.stop_reason not in ("end_turn", "max_tokens", "max_turn_requests"):
                raise HarnessRefusedError(
                    f"{member.name}'s turn ended {turn.stop_reason}: {answer or 'nothing said'}"
                )
            return answer

        return run

    return factory


__all__ = ["HarnessRefusedError", "RunFn", "catalogue_command", "harness_run_factory"]
