# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""CLI for agent-teams — manage and operate agent teams from the terminal.

Built with `Typer <https://typer.tiangolo.com/>`_.  Entry point is
``agent-teams`` (configured in pyproject.toml ``[project.scripts]``).

Usage::

    # Start the API server
    agent-teams serve --port 8765

    # Create a team from a YAML/JSON config file
    agent-teams create team.yaml

    # List teams
    agent-teams list

    # Show team status
    agent-teams status <team-id>

    # Assign a task
    agent-teams assign <team-id> "Analyse quarterly data"

    # Start / stop / pause / resume
    agent-teams start <team-id>
    agent-teams stop  <team-id>

    # Stream events (Server-Sent Events)
    agent-teams events <team-id>

    # Get team metrics
    agent-teams metrics <team-id>

    # Delete a team
    agent-teams delete <team-id>
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(
    name="agent-teams",
    help="Manage and orchestrate AI agent teams.",
    no_args_is_help=True,
)


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


@app.command("create")
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


@app.command("list")
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


@app.command()
def status(
    team_id: str = typer.Argument(..., help="Team ID"),
    server: str = typer.Option("localhost:8765", help="Server address"),
) -> None:
    """Show detailed status of a team."""
    resp = _request("get", f"http://{server}/api/teams/{team_id}")
    _print_json(resp.json())


@app.command()
def start(
    team_id: str = typer.Argument(..., help="Team ID"),
    server: str = typer.Option("localhost:8765", help="Server address"),
) -> None:
    """Start a team."""
    resp = _request("post", f"http://{server}/api/teams/{team_id}/start")
    typer.echo(f"Team {team_id} started.")


@app.command()
def stop(
    team_id: str = typer.Argument(..., help="Team ID"),
    server: str = typer.Option("localhost:8765", help="Server address"),
) -> None:
    """Stop a team."""
    resp = _request("post", f"http://{server}/api/teams/{team_id}/stop")
    typer.echo(f"Team {team_id} stopped.")


@app.command()
def pause(
    team_id: str = typer.Argument(..., help="Team ID"),
    server: str = typer.Option("localhost:8765", help="Server address"),
) -> None:
    """Pause a running team."""
    resp = _request("post", f"http://{server}/api/teams/{team_id}/pause")
    typer.echo(f"Team {team_id} paused.")


@app.command()
def resume(
    team_id: str = typer.Argument(..., help="Team ID"),
    server: str = typer.Option("localhost:8765", help="Server address"),
) -> None:
    """Resume a paused team."""
    resp = _request("post", f"http://{server}/api/teams/{team_id}/resume")
    typer.echo(f"Team {team_id} resumed.")


@app.command()
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


@app.command()
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


@app.command()
def metrics(
    team_id: str = typer.Argument(..., help="Team ID"),
    server: str = typer.Option("localhost:8765", help="Server address"),
) -> None:
    """Show metrics for a team."""
    resp = _request("get", f"http://{server}/api/teams/{team_id}/metrics")
    _print_json(resp.json())


@app.command()
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
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point."""
    app()


if __name__ == "__main__":
    main()
