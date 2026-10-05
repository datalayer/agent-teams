# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""``agent-teams demo``: the landing's demo team, on Datalayer, as you.

    datalayer agent-teams demo deploy [--dry-run]
    datalayer agent-teams demo status
    datalayer agent-teams demo url
    datalayer agent-teams demo stop

``deploy`` reads the team spec (``sales-and-accounting`` by default) and puts
every member that runs on a runtime on a cloud runtime of your own, served
over A2A and open to the landing's visitors; it prints the address and the
landing setting that takes it. Run again, it reuses the runtime it launched.
With ``DATALAYER_MAGIC_API_KEY`` set, a runtime it launches is unmetered: it
consumes no credits and never expires. See :mod:`agent_teams.demo`.
"""

from __future__ import annotations

from typing import Any, Optional

import typer
from datalayer_core.cli.commands.contents import OutputFormat
from datalayer_core.cli.commands.orchestration_common import (
    OrchestrationCommandError,
    console,
    emit,
    error_console,
    orchestration_command,
    output_option,
)
from rich.table import Table

from agent_teams import demo

demo_app = typer.Typer(
    name="demo",
    help=(
        "The landing's demo team on Datalayer, under your account: deploy the members "
        "that run in the cloud, see how they stand, print their address, stop them."
    ),
    no_args_is_help=True,
)


def team_option() -> Any:
    return typer.Option(
        demo.DEMO_TEAM, "--team", "-t", help="The team spec, as agentspecs names it."
    )


def say(text: str) -> None:
    console.print(text, markup=False, highlight=False)


@demo_app.command("deploy")
@orchestration_command
def deploy(
    team_id: str = team_option(),
    environment: Optional[str] = typer.Option(
        None,
        "--environment",
        "-e",
        help="The environment of a new runtime; the one that holds agents by default.",
    ),
    minutes: int = typer.Option(
        demo.DEFAULT_MINUTES,
        "--minutes",
        "-m",
        min=1,
        max=demo.MAX_MINUTES,
        help=(
            f"How long a new runtime is reserved, at most {demo.MAX_MINUTES}; "
            "ignored with DATALAYER_MAGIC_API_KEY set, which launches it to never expire."
        ),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Say what would be launched and configured, and launch nothing.",
    ),
    output: OutputFormat = output_option(),
) -> None:
    """Deploy the team's cloud members as you, over A2A, open to visitors; reuse what runs."""
    team = demo.read_team(team_id)
    cloud = demo.connect()
    handle = cloud.whoami()
    steps = demo.plan(cloud, team, environment=environment, minutes=minutes)
    quiet = output is not OutputFormat.TABLE
    if not quiet:
        say(f"{team.name}: deploying as {handle}{' (dry run)' if dry_run else ''}.")
        for step in steps:
            say(_step_words(cloud, step))
    if dry_run:
        rows = [_dry_row(cloud, step) for step in steps]
        if emit({"account": handle, "team": team.id, "dryRun": True, "members": rows}, output):
            return
        for row in rows:
            say(
                f"  then POST {row['configure']} with {row['app']}, a2a: true, "
                f"public_url: {row['publicUrl']}, {demo.VISITORS_FIELD}: true"
            )
            if row["reuse"] is None:
                say(f"  unmetered: {'yes' if row['unmetered'] else 'no'}")
        say("Dry run: nothing was launched or configured.")
        return
    rows = [_deploy_one(cloud, step, quiet=quiet) for step in steps]
    if emit({"account": handle, "team": team.id, "dryRun": False, "members": rows}, output):
        return
    for row in rows:
        say(
            f"{row['label']} is served over A2A on runtime {row['runtime']}, "
            f"open to visitors ({_left_words(row)})."
        )
        say(f"  A2A:  {row['a2a']}")
        say(f"  Card: {row['card']}")
    say("Set on the landing:")
    for row in rows:
        say(f"  {row['setting']} = {row['a2a']}")


def _is_unmetered(runtime: Any) -> bool:
    return bool(getattr(runtime, "unmetered", False))


def _minutes_left(cloud: Any, runtime: Any) -> int | None:
    """The minutes a runtime has left; None for an unmetered one, which never expires."""
    return None if _is_unmetered(runtime) else cloud.minutes_left(runtime)


def _left_words(row: dict[str, Any]) -> str:
    """The time a row's runtime has left, in words."""
    if row.get("unmetered"):
        return "never expires"
    left = row.get("minutesLeft")
    return f"{left if left is not None else '?'} min left"


def _step_words(cloud: Any, step: demo.Step) -> str:
    if step.reuses:
        left = _left_words(
            {
                "unmetered": _is_unmetered(step.runtime),
                "minutesLeft": _minutes_left(cloud, step.runtime),
            }
        )
        return f"{step.member.label}: reusing runtime {step.runtime.uid} ({left})."
    if step.unmetered:
        return (
            f"{step.member.label}: launching {step.name} in {step.environment.name}, "
            "unmetered (no credits, never expires)."
        )
    return (
        f"{step.member.label}: launching {step.name} in {step.environment.name} "
        f"for {step.minutes} min (at most {step.cost:.2f} credits)."
    )


def _dry_row(cloud: Any, step: demo.Step) -> dict[str, Any]:
    base = cloud.base(step.runtime) if step.reuses else "<the new runtime>"
    return {
        "member": step.member.id,
        "app": f"{step.member.app_id}:{step.member.document.get('version', '')}",
        "runtimeName": step.name,
        "reuse": step.runtime.uid if step.reuses else None,
        "environment": None if step.reuses else step.environment.name,
        "minutes": None if step.reuses or step.unmetered else step.minutes,
        "maxCredits": None if step.reuses else round(step.cost, 2),
        "unmetered": _is_unmetered(step.runtime) if step.reuses else step.unmetered,
        "configure": f"{base}/api/v1/apps/configure",
        "publicUrl": base,
        "setting": demo.landing_setting(step.member.id),
    }


def _deploy_one(cloud: Any, step: demo.Step, *, quiet: bool) -> dict[str, Any]:
    """Launch or reuse a member's runtime and configure it; a runtime launched here
    is given back when anything after its launch fails.

    Anything: a refusal said in words, and as much a timeout or a refused
    connection from the runtime, an answer that is not JSON, or one without
    the address — whatever escaped would leave the runtime running, and
    charging, with nobody using it.
    """
    runtime = step.runtime
    launched = runtime is None
    if launched:
        runtime = cloud.create(step.name, step.environment, step.minutes)
        if not quiet:
            say(f"{step.member.label}: waiting for runtime {runtime.uid}…")
    try:
        base = cloud.base(runtime)
        if launched and not demo.wait_until_ready(cloud, base):
            raise OrchestrationCommandError(f"The runtime {runtime.uid} did not come up in time.")
        served = demo.configure(cloud, step.member, base)
        address = str(served["url"])
        return {
            "member": step.member.id,
            "label": step.member.label,
            "runtime": str(runtime.uid),
            "reused": not launched,
            "unmetered": _is_unmetered(runtime),
            "minutesLeft": _minutes_left(cloud, runtime),
            "a2a": address,
            "card": served.get("card") or demo.card_address(address),
            "setting": demo.landing_setting(step.member.id),
        }
    except Exception as error:
        message = (
            str(error)
            if isinstance(error, OrchestrationCommandError)
            else f"{step.member.label} was not configured on {runtime.uid} "
            f"({type(error).__name__}: {error})."
        )
        if not launched:
            raise OrchestrationCommandError(message) from None
        stopped = cloud.stop(str(runtime.uid))
        raise OrchestrationCommandError(
            f"{message} "
            + (
                f"The runtime {runtime.uid} was stopped."
                if stopped
                else f"Stopping {runtime.uid} failed: `datalayer agent-teams demo stop`."
            )
        ) from None


def _state_rows(cloud: Any, team: demo.DemoTeam) -> list[dict[str, Any]]:
    running = cloud.running()
    rows: list[dict[str, Any]] = []
    for member in team.members:
        row: dict[str, Any] = {
            "member": member.id,
            "runsIn": member.runs_in or "anywhere",
            "state": "in the visitor's browser",
            "a2a": None,
            "runtime": None,
            "unmetered": False,
            "minutesLeft": None,
        }
        if member.hosted:
            runtime = demo.find_runtime(running, demo.runtime_name(team.id, member.id))
            if runtime is None:
                row["state"] = "not deployed"
            else:
                address = demo.a2a_address(cloud.base(runtime), member.app_id)
                answered = demo.serving(cloud, address)
                row.update(
                    a2a=address,
                    runtime=str(runtime.uid),
                    unmetered=_is_unmetered(runtime),
                    minutesLeft=_minutes_left(cloud, runtime),
                    state=(
                        "serving"
                        if answered == 200
                        else "not answering"
                        if answered is None
                        else f"not serving (HTTP {answered})"
                    ),
                )
        rows.append(row)
    return rows


@demo_app.command("status")
@orchestration_command
def status(team_id: str = team_option(), output: OutputFormat = output_option()) -> None:
    """Each member: where it runs, how it stands, its address, its runtime and the time left."""
    team = demo.read_team(team_id)
    rows = _state_rows(demo.connect(), team)
    if emit({"team": team.id, "members": rows}, output):
        return
    table = Table(title=team.name)
    for column in ("Member", "Runs in", "State", "A2A", "Runtime", "Time left"):
        table.add_column(column, overflow="fold")
    for row in rows:
        left = row["minutesLeft"]
        table.add_row(
            row["member"],
            row["runsIn"],
            row["state"],
            row["a2a"] or "",
            row["runtime"] or "",
            "never" if row["unmetered"] else f"{left} min" if left is not None else "",
        )
    console.print(table)


@demo_app.command("url")
@orchestration_command
def url(
    team_id: str = team_option(),
    member_id: Optional[str] = typer.Option(
        None, "--member", help="The member; the one on a runtime when there is one."
    ),
) -> None:
    """Print only a deployed member's A2A address, for scripts; fail when it is not serving."""
    team = demo.read_team(team_id)
    member = team.member(member_id)
    cloud = demo.connect()
    runtime = demo.find_runtime(cloud.running(), demo.runtime_name(team.id, member.id))
    if runtime is None:
        raise OrchestrationCommandError(
            f"{member.label} is not deployed: `datalayer agent-teams demo deploy`."
        )
    address = demo.a2a_address(cloud.base(runtime), member.app_id)
    answered = demo.serving(cloud, address)
    if answered != 200:
        raise OrchestrationCommandError(
            f"{member.label} runs on {runtime.uid} and is not serving "
            f"({'no answer' if answered is None else f'HTTP {answered}'}): "
            "`datalayer agent-teams demo deploy` configures it again."
        )
    typer.echo(address)


@demo_app.command("stop")
@orchestration_command
def stop(team_id: str = team_option()) -> None:
    """Stop the team's demo runtimes."""
    team = demo.read_team(team_id)
    cloud = demo.connect()
    running = cloud.running()
    names = {demo.runtime_name(team.id, member.id) for member in team.hosted}
    found = [runtime for runtime in running if str(getattr(runtime, "name", "")) in names]
    if not found:
        say(f"{team.name}: no demo runtime is running.")
        return
    failed = []
    for runtime in found:
        if cloud.stop(str(runtime.uid)):
            say(f"Stopped {runtime.uid} ({runtime.name}).")
        else:
            failed.append(str(runtime.uid))
    if failed:
        error_console.print(
            f"Datalayer did not stop {', '.join(failed)}.", style="red", markup=False
        )
        raise typer.Exit(1)


def register(app: typer.Typer) -> None:
    """Add ``demo`` to the ``agent-teams`` application."""
    app.add_typer(demo_app)


__all__ = ["demo_app", "register"]
