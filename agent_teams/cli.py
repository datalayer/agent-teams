# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""CLI for agent-teams — teams of agents, from the terminal.

Built with `Typer <https://typer.tiangolo.com/>`_. It is both the
``agent-teams`` executable (``[project.scripts]``) and, through
``agent_teams.reactor_extension``, the ``agent-teams`` group of the Datalayer
CLI: ``datalayer agent-teams …`` wherever both are installed.

Teams on Datalayer — the catalogued teams, run on the orchestration control
plane (``agent_teams.platform_cli``, needs ``agent-teams[datalayer]``)::

    agent-teams list
    agent-teams show notebook-benchmark
    agent-teams start notebook-benchmark --goal "Profile customers.csv" --watch
    agent-teams runs notebook-benchmark --active
    agent-teams status <run>
    agent-teams monitor | steer | pause | resume | cancel | terminate | artifacts <run>

The landing's demo team on Datalayer, under your account (``agent_teams.demo_cli``,
needs ``agent-teams[demo]``)::

    agent-teams demo deploy [--dry-run]
    agent-teams demo status
    agent-teams demo url
    agent-teams demo stop

A self-hosted agent-teams server, and the teams it holds::

    agent-teams serve --port 8765
    agent-teams local create team.yaml
    agent-teams local list
    agent-teams local status <team-id>
    agent-teams local assign <team-id> "Analyse quarterly data"
    agent-teams local start | stop | pause | resume | delete <team-id>
    agent-teams local events <team-id>
    agent-teams local metrics <team-id>
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(
    name="agent-teams",
    help=(
        "Agent teams: the catalogued teams run on Datalayer — list, show, start one, "
        "follow, steer, pause, resume, cancel or terminate a run — and `local`, the teams "
        "of a self-hosted agent-teams server (`agent-teams serve`)."
    ),
    no_args_is_help=True,
)

#: The teams a self-hosted server holds (`agent-teams serve`). Under their own
#: group since the platform's commands took the top level: five of the names —
#: list, status, start, pause, resume — are the same words for other things.
local_app = typer.Typer(
    name="local",
    help=(
        "The teams of a self-hosted agent-teams server: create, list, assign, "
        "start, stop, pause, resume, delete, events, metrics."
    ),
    no_args_is_help=True,
)
app.add_typer(local_app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def _request(method: str, url: str, **kwargs):
    """Synchronous HTTP request using httpx."""
    import httpx

    with httpx.Client(timeout=30) as client:
        resp = getattr(client, method)(url, **kwargs)
        resp.raise_for_status()
        return resp


def _print_json(data) -> None:
    typer.echo(json.dumps(data, indent=2, default=str))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind address"),
    port: int = typer.Option(8765, help="Port number"),
    reload: bool = typer.Option(False, help="Enable auto-reload for development"),
) -> None:
    """Start the agent-teams API server."""
    import uvicorn

    typer.echo(f"Starting agent-teams server on {host}:{port}")
    uvicorn.run(
        "agent_teams.app:create_app",
        host=host,
        port=port,
        reload=reload,
        factory=True,
    )


@local_app.command("create")
def create_team(
    config_file: Path = typer.Argument(..., help="Path to team config (YAML or JSON)"),
    server: str = typer.Option("localhost:8765", help="Server address"),
) -> None:
    """Create a new team from a configuration file."""
    if not config_file.exists():
        typer.echo(f"Config file not found: {config_file}", err=True)
        raise typer.Exit(1)

    text = config_file.read_text()
    if config_file.suffix in (".yaml", ".yml"):
        try:
            import yaml
            data = yaml.safe_load(text)
        except ImportError:
            typer.echo("PyYAML is required for YAML configs: pip install pyyaml", err=True)
            raise typer.Exit(1)
    else:
        data = json.loads(text)

    resp = _request("post", f"http://{server}/api/teams", json=data)
    _print_json(resp.json())
    typer.echo(f"Team created: {resp.json().get('id', 'unknown')}")


@local_app.command("list")
def list_teams(
    server: str = typer.Option("localhost:8765", help="Server address"),
) -> None:
    """List all teams."""
    resp = _request("get", f"http://{server}/api/teams")
    teams = resp.json()
    if not teams:
        typer.echo("No teams found.")
        return
    for t in teams:
        status = t.get("status", "?")
        name = t.get("name", "unnamed")
        tid = t.get("id", "?")
        members = t.get("member_count", 0)
        typer.echo(f"  {tid}  {name:30s}  {status:12s}  members={members}")


@local_app.command()
def status(
    team_id: str = typer.Argument(..., help="Team ID"),
    server: str = typer.Option("localhost:8765", help="Server address"),
) -> None:
    """Show detailed status of a team."""
    resp = _request("get", f"http://{server}/api/teams/{team_id}")
    _print_json(resp.json())


@local_app.command()
def start(
    team_id: str = typer.Argument(..., help="Team ID"),
    server: str = typer.Option("localhost:8765", help="Server address"),
) -> None:
    """Start a team."""
    resp = _request("post", f"http://{server}/api/teams/{team_id}/start")
    typer.echo(f"Team {team_id} started.")


@local_app.command()
def stop(
    team_id: str = typer.Argument(..., help="Team ID"),
    server: str = typer.Option("localhost:8765", help="Server address"),
) -> None:
    """Stop a team."""
    resp = _request("post", f"http://{server}/api/teams/{team_id}/stop")
    typer.echo(f"Team {team_id} stopped.")


@local_app.command()
def pause(
    team_id: str = typer.Argument(..., help="Team ID"),
    server: str = typer.Option("localhost:8765", help="Server address"),
) -> None:
    """Pause a running team."""
    resp = _request("post", f"http://{server}/api/teams/{team_id}/pause")
    typer.echo(f"Team {team_id} paused.")


@local_app.command()
def resume(
    team_id: str = typer.Argument(..., help="Team ID"),
    server: str = typer.Option("localhost:8765", help="Server address"),
) -> None:
    """Resume a paused team."""
    resp = _request("post", f"http://{server}/api/teams/{team_id}/resume")
    typer.echo(f"Team {team_id} resumed.")


@local_app.command()
def delete(
    team_id: str = typer.Argument(..., help="Team ID"),
    server: str = typer.Option("localhost:8765", help="Server address"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Delete a team."""
    if not force:
        confirm = typer.confirm(f"Delete team {team_id}?")
        if not confirm:
            raise typer.Abort()
    resp = _request("delete", f"http://{server}/api/teams/{team_id}")
    typer.echo(f"Team {team_id} deleted.")


@local_app.command()
def assign(
    team_id: str = typer.Argument(..., help="Team ID"),
    title: str = typer.Argument(..., help="Task title"),
    description: str = typer.Option("", help="Task description"),
    priority: int = typer.Option(3, help="Task priority (1=critical, 5=low)"),
    assigned_to: Optional[str] = typer.Option(None, help="Assign to specific member"),
    server: str = typer.Option("localhost:8765", help="Server address"),
) -> None:
    """Assign a task to the team."""
    payload = {
        "title": title,
        "description": description or title,
        "priority": priority,
    }
    if assigned_to:
        payload["assigned_to"] = assigned_to
    resp = _request("post", f"http://{server}/api/teams/{team_id}/tasks", json=payload)
    _print_json(resp.json())


@local_app.command()
def metrics(
    team_id: str = typer.Argument(..., help="Team ID"),
    server: str = typer.Option("localhost:8765", help="Server address"),
) -> None:
    """Show metrics for a team."""
    resp = _request("get", f"http://{server}/api/teams/{team_id}/metrics")
    _print_json(resp.json())


@local_app.command()
def events(
    team_id: str = typer.Argument(..., help="Team ID"),
    limit: int = typer.Option(50, help="Maximum events to show"),
    server: str = typer.Option("localhost:8765", help="Server address"),
) -> None:
    """Show recent events for a team."""
    resp = _request("get", f"http://{server}/api/teams/{team_id}/events", params={"limit": limit})
    events_list = resp.json()
    if not events_list:
        typer.echo("No events.")
        return
    for ev in events_list:
        ts = ev.get("timestamp", "")
        etype = ev.get("type", "?")
        source = ev.get("source", "?")
        msg = ev.get("message", "")
        typer.echo(f"  [{ts}] {etype:25s} {source:15s} {msg}")


# ---------------------------------------------------------------------------
# Teams on Datalayer
# ---------------------------------------------------------------------------

# Only with Datalayer core (`agent-teams[datalayer]`), which the Datalayer CLI
# hosting this group always has. Asked by its presence rather than by catching
# an ImportError, so a mistake inside the commands is never mistaken for it.
if importlib.util.find_spec("datalayer_core") is not None:
    from agent_teams.platform_cli import register as _register_platform

    _register_platform(app)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point."""
    app()


if __name__ == "__main__":
    main()
