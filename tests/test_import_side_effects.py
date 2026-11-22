# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Importing agent-teams builds nothing: no app, no platform, no extension discovery.

Each import runs in a fresh interpreter, with ``importlib.metadata.entry_points``
wrapped before anything is imported so a lookup of the reactor's extension
group (what ``discover()`` does) is recorded.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

_PROBE = """
import importlib.metadata as metadata
import json
import sys

groups = []
_entry_points = metadata.entry_points

def recording(*args, **kwargs):
    groups.append(kwargs.get("group"))
    return _entry_points(*args, **kwargs)

metadata.entry_points = recording

import {module}

print(json.dumps({{
    "app_imported": "agent_teams.app" in sys.modules,
    "groups": groups,
}}))
"""


def _probe(module: str) -> dict:
    result = subprocess.run(  # noqa: S603 - our own interpreter and probe
        [sys.executable, "-c", _PROBE.format(module=module)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("module", ["agent_teams", "agent_teams.a2a.orchestration_extension"])
def test_import_builds_no_app_and_discovers_nothing(module: str) -> None:
    probe = _probe(module)
    assert probe["app_imported"] is False
    assert "datalayer.reactor.extensions" not in probe["groups"]


def test_create_app_stays_reachable_from_the_package() -> None:
    import agent_teams
    from agent_teams.app import create_app

    assert agent_teams.create_app is create_app
    assert "create_app" in agent_teams.__all__


def test_app_module_has_no_module_level_app() -> None:
    import agent_teams.app

    assert not hasattr(agent_teams.app, "app")
