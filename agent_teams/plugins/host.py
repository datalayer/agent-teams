# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""The team manager's plugin host (MH-F07): the points read, the refusals kept.

The platform is the reactor's :class:`~reactor.PluginPlatform` with the
``agent-teams`` extension registered on it — its built-in plugin contributing
the ``a2a`` runner, the ``harness`` runner when one is configured, and the
three orchestrations — and every other extension installed beside it
discovered under ``datalayer.reactor.extensions``, the reactor's group. A
host contributes what it has of its own: the ``model`` runner (how a model
with a prompt runs), a test its echo.

A point is read through :func:`accepted`: what passed :func:`points.check`,
in order; what did not is in :func:`refusals`. :func:`runner_for` and
:func:`orchestration_for` answer one contribution by id, or refuse in a
sentence that says what to contribute.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from importlib.metadata import entry_points
from typing import Any

from reactor import ContributionPoint, PluginManifest, PluginPlatform
from reactor.extensions import EXTENSION_ENTRY_POINT_GROUP

from .points import (
    MEMBER_RUNNERS,
    ORCHESTRATIONS,
    POINTS,
    MemberRunner,
    Orchestration,
    Refusal,
    RunFactory,
    check,
)

logger = logging.getLogger(__name__)

#: The name of agent-teams' own extension and of its built-in plugin.
EXTENSION = "agent-teams"
BUILTIN_PLUGIN = "agent-teams.runs"

#: Entry points that did not resolve, per platform: ``name -> (entry point, error)``.
_FAILED: dict[int, dict[str, tuple[str, str]]] = {}


def discover(platform: PluginPlatform, group: str = "") -> list[str]:
    """Register every extension installed under ``group`` (the reactor's when
    none is named) that the platform does not hold yet; one that does not load
    is kept as failed with its entry point and error. The names registered.
    """
    group = group or getattr(platform, "extension_group", "") or EXTENSION_ENTRY_POINT_GROUP
    failed = _FAILED.setdefault(id(platform), {})
    registered: list[str] = []
    held = {entry["name"] for entry in platform.list_extensions()}
    for entry_point in entry_points(group=group):
        try:
            extension = entry_point.load()()
            name = getattr(extension, "name", None)
            if not isinstance(name, str) or not name:
                raise TypeError(f"{entry_point.value} did not answer a ReactorExtension")
            if name in held:
                failed.pop(entry_point.name, None)
                continue
            platform.register_extension_object(extension)
            held.add(name)
            registered.append(name)
            failed.pop(entry_point.name, None)
        except Exception as error:
            reported = f"{type(error).__name__}: {error}"
            if failed.get(entry_point.name) != (entry_point.value, reported):
                logger.warning(
                    "Extension %r (%s) could not be loaded: %s",
                    entry_point.name,
                    entry_point.value,
                    reported,
                )
            failed[entry_point.name] = (entry_point.value, reported)
    return registered


def failed_extensions(platform: PluginPlatform) -> dict[str, tuple[str, str]]:
    """The entry points that did not resolve on this platform: name -> (entry point, error)."""
    return dict(_FAILED.get(id(platform), {}))


def teams_platform(
    platform: PluginPlatform | None = None,
    *,
    model: RunFactory | None = None,
    harness: Any | None = None,
    plugins: Iterable[tuple[PluginManifest, Any]] = (),
    discover_installed: bool = True,
) -> PluginPlatform:
    """A platform with the ``agent-teams`` extension on it.

    ``model`` is the host's own runner of a model member (contributed by the
    built-in plugin under ``model`` as the host's); ``harness`` the harness
    runner's configuration — a :class:`~agent_teams.harness.HarnessRunner`,
    or ``None`` to read it from the environment (``AGENT_TEAMS_WORKSPACE``);
    ``plugins`` further ``(manifest, implementation)`` pairs registered beside
    the built-in (a test's, a server's). Everything installed beside is
    discovered once under the reactor's group when ``discover_installed``.
    """
    from .extension import extension

    platform = PluginPlatform() if platform is None else platform
    if EXTENSION not in {entry["name"] for entry in platform.list_extensions()}:
        platform.register_extension_object(extension(model=model, harness=harness))
    for manifest, implementation in plugins:
        if not platform.has_plugin(manifest.name):
            platform.register_plugin(manifest, implementation)
    if discover_installed:
        discover(platform)
    return platform


def accepted(platform: PluginPlatform, point: ContributionPoint[Any]) -> list[Any]:
    """The contributions of a point the seam accepts, in order."""
    passed, _ = check(point, platform.get_contributions(point))
    return passed


def refusals(platform: PluginPlatform) -> list[Refusal]:
    """Every contribution refused at the seam, over every point."""
    refused: list[Refusal] = []
    for point in POINTS:
        refused.extend(check(point, platform.get_contributions(point))[1])
    return refused


_RUNNER_HINTS: Mapping[str, str] = {
    "model": (
        "a host contributes how a model with a prompt runs "
        "(teams_platform(model=…), or a plugin contributing a MemberRunner('model', …))"
    ),
    "harness": (
        "a coding agent runs behind the Shield: contribute "
        "agent_teams.harness.HarnessRunner — teams_platform(harness=HarnessRunner(workspace, policy=…)), "
        "or AGENT_TEAMS_WORKSPACE in the environment (agent-teams[shield])"
    ),
    "a2a": "agent-teams contributes it with fasta2a installed (agent-teams[a2a])",
}


def runner_for(platform: PluginPlatform, kind: str) -> MemberRunner:
    """The runner contributed for a member kind, or a refusal that says what to contribute."""
    for contribution in accepted(platform, MEMBER_RUNNERS):
        if contribution.id == kind:
            return contribution.value
    raise ValueError(
        f"No {MEMBER_RUNNERS.id} is contributed for a {kind} member: "
        f"{_RUNNER_HINTS.get(kind, 'contribute one under that kind')}."
    )


def orchestration_for(platform: PluginPlatform, mode: Any) -> Orchestration:
    """The orchestration contributed for an execution mode, or a refusal naming the modes contributed.

    ``graph`` falls back to nothing: a mode nobody contributes is refused, never
    served by another in its place.
    """
    word = getattr(mode, "value", mode)
    found = accepted(platform, ORCHESTRATIONS)
    for contribution in found:
        if contribution.id == word:
            return contribution.value
    known = ", ".join(one.id for one in found) or "none"
    raise ValueError(
        f"No {ORCHESTRATIONS.id} is contributed for execution mode {word!r}: one of {known}"
        + (" ('graph' is not served by 'supervisor' in its place)" if word == "graph" else "")
        + "."
    )


def describe_plugins(platform: PluginPlatform) -> dict[str, Any]:
    """What the host holds: each point's accepted contributions, the refusals, the extensions that did not load."""
    return {
        "points": {
            point.id: [{"plugin": one.plugin, "id": one.id} for one in accepted(platform, point)]
            for point in POINTS
        },
        "refused": [one.line() for one in refusals(platform)],
        "failed": {
            name: {"entry_point": entry, "error": error}
            for name, (entry, error) in failed_extensions(platform).items()
        },
    }


__all__ = [
    "BUILTIN_PLUGIN",
    "EXTENSION",
    "accepted",
    "describe_plugins",
    "discover",
    "failed_extensions",
    "orchestration_for",
    "refusals",
    "runner_for",
    "teams_platform",
]
