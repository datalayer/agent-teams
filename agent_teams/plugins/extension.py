# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""The ``agent-teams`` reactor extension (MH-F07): the run's points, and the
built-in plugin that contributes what agent-teams ships.

Contributed at ``order=0``, first — the seam keeps one contribution per id
and refuses a second by name:

- ``teams.member.runner``: ``a2a`` (a remote worker over fasta2a's client),
  ``harness`` (a coding agent behind the Shield, one session for the run,
  :class:`~agent_teams.harness.HarnessRunner`) when one is configured, and
  ``model`` when the host handed the platform its own;
- ``teams.session.orchestration``: ``sequential``, ``parallel`` and
  ``supervisor``, each owning its run's seat sessions in the local stores
  (:class:`~agent_teams.sessions.SeatSessions`).

Advertised under ``datalayer.reactor.extensions`` (``pyproject.toml``), so a
reactor host that discovers extensions gets the points and their built-ins.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from reactor import ExtensionManifest, PluginManifest, ReactorExtension, hookimpl

from ..__version__ import __version__
from .host import BUILTIN_PLUGIN, EXTENSION
from .points import MEMBER_RUNNERS, ORCHESTRATIONS, POINTS, MemberRunner, Orchestration, RunFactory

logger = logging.getLogger(__name__)

#: The order of the built-in contributions: first.
DEFAULT_ORDER = 0

#: Where a self-hosted team's harness members work, when the environment says.
WORKSPACE_VARIABLE = "AGENT_TEAMS_WORKSPACE"
#: The Shield's policy file for them, when the environment says.
POLICY_VARIABLE = "AGENT_TEAMS_SHIELD_POLICY"


def local_sessions(**run: Any) -> Any:
    """The seat sessions of a run, in the local stores."""
    from ..sessions import SeatSessions

    return SeatSessions(**run)


def _orchestrations() -> list[Orchestration]:
    from ..orchestration.parallel import ParallelOrchestrator
    from ..orchestration.sequential import SequentialOrchestrator
    from ..orchestration.supervisor import SupervisorOrchestrator

    return [
        Orchestration("sequential", SequentialOrchestrator, local_sessions),
        Orchestration("parallel", ParallelOrchestrator, local_sessions),
        Orchestration("supervisor", SupervisorOrchestrator, local_sessions),
    ]


def harness_from_environment() -> Optional[Any]:
    """The harness runner the environment configures, or ``None`` when it names no workspace."""
    workspace = os.environ.get(WORKSPACE_VARIABLE, "").strip()
    if not workspace:
        return None
    from ..harness import HarnessRunner

    return HarnessRunner(workspace, policy=os.environ.get(POLICY_VARIABLE, "").strip() or None)


class RunsPlugin:
    """Contributes the runners and the orchestrations agent-teams ships."""

    def __init__(self, *, model: Optional[RunFactory] = None, harness: Optional[Any] = None) -> None:
        self._model = model
        self._harness = harness

    @hookimpl
    def provide_contributions(self, contributions: Any) -> None:
        from .runners import a2a_runner

        contributions.contribute(MEMBER_RUNNERS, a2a_runner(), contribution_id="a2a", order=DEFAULT_ORDER)
        harness = self._harness if self._harness is not None else harness_from_environment()
        if harness is not None:
            contributions.contribute(
                MEMBER_RUNNERS, harness.as_runner(), contribution_id="harness", order=DEFAULT_ORDER
            )
        if self._model is not None:
            contributions.contribute(
                MEMBER_RUNNERS,
                MemberRunner("model", self._model),
                contribution_id="model",
                order=DEFAULT_ORDER,
            )
        for orchestration in _orchestrations():
            contributions.contribute(
                ORCHESTRATIONS, orchestration, contribution_id=orchestration.id, order=DEFAULT_ORDER
            )


def plugin(*, model: Optional[RunFactory] = None, harness: Optional[Any] = None) -> tuple[PluginManifest, RunsPlugin]:
    """The built-in plugin, as the extension delivers it."""
    return (
        PluginManifest(
            name=BUILTIN_PLUGIN,
            version=__version__,
            description=(
                "A team run's points — teams.member.runner, teams.session.orchestration — "
                "and what agent-teams contributes to each."
            ),
            tags=["agent-teams", "sessions"],
            contribution_points=[point.id for point in POINTS],
        ),
        RunsPlugin(model=model, harness=harness),
    )


def extension(*, model: Optional[RunFactory] = None, harness: Optional[Any] = None) -> ReactorExtension:
    """The entry point (``datalayer.reactor.extensions``)."""
    return ReactorExtension(
        manifest=ExtensionManifest(
            name=EXTENSION,
            version=__version__,
            display_name="Agent teams",
            description=(
                "A team run as a session: how each kind of seat runs a task, and how a run "
                "coordinates its seats and owns their sessions, per execution mode."
            ),
            octicon="people",
        ),
        plugins=[plugin(model=model, harness=harness)],
    )


__all__ = [
    "DEFAULT_ORDER",
    "POLICY_VARIABLE",
    "WORKSPACE_VARIABLE",
    "RunsPlugin",
    "extension",
    "harness_from_environment",
    "local_sessions",
    "plugin",
]
