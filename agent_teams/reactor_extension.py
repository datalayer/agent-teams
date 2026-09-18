# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""What agent-teams contributes to the Datalayer CLI, as a reactor plugin.

`datalayer agent-teams …` is the way to teams from the platform's command
line: the catalogued teams run on Datalayer, and the teams of a self-hosted
agent-teams server under `local`. The host discovers this extension through
the reactor (`reactor.cli.extend` over the `datalayer.cli` group) and hands it
its Typer application; the extension adds the `agent-teams` group — the same
application the `agent-teams` executable runs, so the two cannot drift.

Advertised by this distribution's entry points::

    [project.entry-points."datalayer.cli"]
    agent-teams = "agent_teams.reactor_extension:plugin"

    [project.entry-points."datalayer.reactor.cli"]
    agent-teams = "agent_teams.reactor_extension:plugin"

so installing agent-teams beside the Datalayer CLI — or the `reactor`
command — is all it takes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from reactor import PluginManifest

from agent_teams.__version__ import __version__

if TYPE_CHECKING:  # pragma: no cover - typing only
    import typer

logger = logging.getLogger(__name__)

#: The identity of the extension, for the reactor.
manifest = PluginManifest(
    name="agent-teams",
    version=__version__,
    description=(
        "Agent teams for the Datalayer CLI: start a catalogued team on a goal, "
        "follow, steer, pause, resume, cancel or terminate its run."
    ),
    author="Datalayer",
    tags=["cli", "agents", "teams", "orchestration"],
)


class AgentTeamsCliExtension:
    """The plugin: registers the `agent-teams` group into the host CLI."""

    def provide_cli(self, cli: typer.Typer) -> None:
        # Imported here, not at the top: the host loads every extension's
        # manifest at start-up, and only the ones it keeps need their commands.
        from agent_teams.cli import app

        cli.add_typer(app)


def plugin() -> tuple[PluginManifest, AgentTeamsCliExtension]:
    """What the `datalayer.cli` and `datalayer.reactor.cli` entry points resolve to."""
    return manifest, AgentTeamsCliExtension()
