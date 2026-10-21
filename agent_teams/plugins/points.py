# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""The contribution points of a team run (MH-F07): what a plugin may offer.

A team is extended through the reactor and nothing else. Two points:

- ``teams.member.runner`` — a :class:`MemberRunner`: how a seat of one kind
  runs a task. One contribution per kind — ``model`` (a model with a prompt),
  ``harness`` (a coding agent behind the Shield), ``a2a`` (a remote worker) —
  chosen by the member's configuration (:func:`runner_kind`): ``harness``
  set → ``harness``; an ``agent_endpoint`` under an A2A team → ``a2a``; else
  ``model``. agent-teams contributes ``a2a`` and ``harness``; the host — a
  server, agent-runtimes, a test — contributes ``model``.
- ``teams.session.orchestration`` — an :class:`Orchestration`: how a run
  coordinates its seats and **owns their sessions** — opens a seat's session
  at its first task, resumes it at the next, ends them with the run — one per
  execution mode (``sequential``, ``parallel``, ``supervisor``). A mode nobody
  contributes (``graph``) is refused in a sentence at start, never served by
  another mode in its place.

**Fail fast at the seam.** A contribution that is not the point's shape, or a
second under an id already contributed, is refused with the plugin's name and
the field (:func:`check`); the host runs with the rest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Coroutine, Dict, List, Optional, Sequence, Tuple

from reactor import Contribution, ContributionPoint, define_contribution_point

from ..types import AgentMemberConfig

#: What a run function is: the prompt and the task's context, the answer.
RunFn = Callable[[str, Dict[str, Any]], Coroutine[Any, Any, str]]

#: Makes a member's run function from its configuration.
RunFactory = Callable[[AgentMemberConfig], RunFn]

#: How a seat of one kind runs a task.
MEMBER_RUNNERS: ContributionPoint["MemberRunner"] = define_contribution_point(
    "teams.member.runner"
)
#: How a run coordinates its seats and owns their sessions, per execution mode.
ORCHESTRATIONS: ContributionPoint["Orchestration"] = define_contribution_point(
    "teams.session.orchestration"
)

#: The points, in the order they are listed.
POINTS: Tuple[ContributionPoint[Any], ...] = (MEMBER_RUNNERS, ORCHESTRATIONS)

#: The kinds of member a runner is contributed for.
RUNNER_KINDS: Tuple[str, ...] = ("model", "harness", "a2a")

#: The execution modes an orchestration may be contributed for.
ORCHESTRATION_MODES: Tuple[str, ...] = ("sequential", "parallel", "supervisor")


class ContributionRefused(ValueError):
    """A contribution the point refuses, said in a sentence naming the plugin."""


@dataclass(frozen=True)
class MemberRunner:
    """How a seat of one kind runs a task.

    ``factory`` makes the run function of a member of this kind; ``end``,
    when given, releases what the runner holds for a member when its run is
    over (a harness's process, its channel) — called with the member's id.
    """

    kind: str
    factory: RunFactory
    end: Optional[Callable[[str], Awaitable[None]]] = None

    @property
    def id(self) -> str:
        return self.kind


@dataclass(frozen=True)
class Orchestration:
    """How a run coordinates its seats and owns their sessions, for one mode.

    ``make`` builds the orchestrator of a run; ``sessions`` builds the owner
    of the run's seat sessions (:class:`agent_teams.sessions.SeatSessions`)
    given the run — the local file store's for agent-teams' own, the control
    plane's for a platform's.
    """

    mode: str
    make: Callable[[], Any]
    sessions: Callable[..., Any]

    @property
    def id(self) -> str:
        return self.mode


def runner_kind(member: AgentMemberConfig, *, a2a: bool = False) -> str:
    """The kind of runner a member takes: ``harness``, ``a2a`` or ``model``.

    ``a2a`` says whether the team talks A2A (its protocol, with fasta2a
    installed): a member with an endpoint is a remote worker only then.
    """
    if member.harness:
        return "harness"
    if a2a and member.agent_endpoint:
        return "a2a"
    return "model"


@dataclass(frozen=True)
class Refusal:
    """A contribution refused at the seam, as the host lists it."""

    plugin: str
    point: str
    contribution_id: str
    reason: str

    def line(self) -> str:
        return f"{self.plugin}: {self.point} {self.contribution_id!r} refused — {self.reason}"


def _sentence(contribution: Contribution[Any], point: ContributionPoint[Any], what: str) -> str:
    return f"{contribution.plugin} contributes {point.id} {contribution.id!r} {what}"


def _check_one(point: ContributionPoint[Any], contribution: Contribution[Any]) -> None:
    """Raise :class:`ContributionRefused` when a contribution is not the point's shape."""
    value = contribution.value
    if point is MEMBER_RUNNERS:
        if not isinstance(value, MemberRunner):
            raise ContributionRefused(
                _sentence(contribution, point, "that is not a MemberRunner (a kind and a factory).")
            )
        if value.kind not in RUNNER_KINDS:
            raise ContributionRefused(
                _sentence(
                    contribution,
                    point,
                    f"for a member kind {value.kind!r} that is none: one of {', '.join(RUNNER_KINDS)}.",
                )
            )
        if value.id != contribution.id:
            raise ContributionRefused(
                _sentence(
                    contribution,
                    point,
                    f"whose runner is for {value.kind!r}: a runner is contributed under its kind.",
                )
            )
        if not callable(value.factory):
            raise ContributionRefused(_sentence(contribution, point, "without a factory to run with."))
        if value.end is not None and not callable(value.end):
            raise ContributionRefused(_sentence(contribution, point, "whose end is not callable."))
        return
    if point is ORCHESTRATIONS:
        if not isinstance(value, Orchestration):
            raise ContributionRefused(
                _sentence(contribution, point, "that is not an Orchestration (a mode, make and sessions).")
            )
        if value.mode not in ORCHESTRATION_MODES:
            raise ContributionRefused(
                _sentence(
                    contribution,
                    point,
                    f"for an execution mode {value.mode!r} that is none: one of {', '.join(ORCHESTRATION_MODES)}.",
                )
            )
        if value.id != contribution.id:
            raise ContributionRefused(
                _sentence(
                    contribution,
                    point,
                    f"whose mode is {value.mode!r}: an orchestration is contributed under its mode.",
                )
            )
        if not callable(value.make) or not callable(value.sessions):
            raise ContributionRefused(
                _sentence(contribution, point, "without make and sessions to build a run with.")
            )
        return


def check(
    point: ContributionPoint[Any], contributions: Sequence[Contribution[Any]]
) -> Tuple[List[Contribution[Any]], List[Refusal]]:
    """The contributions a point accepts, and those it refuses — a malformed
    one, or a second under an id already contributed (the first as the
    registry orders them stands; the built-ins come first at order 0).
    """
    accepted: List[Contribution[Any]] = []
    refused: List[Refusal] = []
    seen: Dict[str, str] = {}
    for contribution in contributions:
        try:
            _check_one(point, contribution)
        except ContributionRefused as why:
            refused.append(Refusal(contribution.plugin, point.id, contribution.id, str(why)))
            continue
        first = seen.get(contribution.id)
        if first is not None:
            refused.append(
                Refusal(
                    contribution.plugin,
                    point.id,
                    contribution.id,
                    f"{contribution.plugin} contributes {point.id} {contribution.id!r}, "
                    f"which {first} contributed first: one contribution per id.",
                )
            )
            continue
        seen[contribution.id] = contribution.plugin
        accepted.append(contribution)
    return accepted, refused


__all__ = [
    "MEMBER_RUNNERS",
    "ORCHESTRATIONS",
    "ORCHESTRATION_MODES",
    "POINTS",
    "RUNNER_KINDS",
    "ContributionRefused",
    "MemberRunner",
    "Orchestration",
    "Refusal",
    "RunFactory",
    "RunFn",
    "check",
    "runner_kind",
]
