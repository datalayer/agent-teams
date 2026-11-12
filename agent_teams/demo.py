# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""A demo team, deployed on Datalayer under the signed-in person's account.

The landing's anonymous page shows a team of two applications: Sales, which
runs in the visitor's browser, and Accounting, which has to run in the cloud.
A team spec says which is which (agentspecs ``teams``: each member's
``runs_in``, and who ``talks_to`` whom). This module deploys every member that
runs on a runtime: one cloud runtime per member, created as the person, named
for the team and the member so that the next deploy finds it again, and
configured with the member's application served over A2A — the runtime's
``POST /api/v1/apps/configure`` with ``a2a: true``, the call
``loop apps run <app> --cloud --a2a`` makes, plus the field that opens the
application to the landing's visitors (:data:`VISITORS_FIELD`).

What is read and what is launched goes through agent-runtimes' client — the
Datalayer client, extended with runtimes — and its launch helpers (the
environments offered, the credits left, what a reservation costs), so the
person is the one the ``datalayer`` CLI signs in as: the stored token or
``DATALAYER_API_KEY``. Nothing here mints a key or prints one.

:class:`Cloud` is the only part that reaches Datalayer; everything else reads
what it answers, so the commands are tested against a fake of it.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from datalayer_core.cli.commands.orchestration_common import OrchestrationCommandError

#: The team the landing shows.
DEMO_TEAM = "sales-and-accounting"

#: The ``/api/v1/apps/configure`` field that opens the served application to
#: the landing's visitors. One constant, so it follows the runtime's name for it.
VISITORS_FIELD = "visitors"

#: What the demo's runtimes are named after: ``<prefix>-<team>-<member>``. A
#: deploy finds its runtime again by this name, so a second deploy reuses it.
RUNTIME_PREFIX = "agent-teams-demo"

#: The longest reservation Datalayer takes, in minutes (agent-runtimes' ``MAX_MINUTES``).
MAX_MINUTES = 480

#: How long a new demo runtime is reserved, in minutes, unless told otherwise.
DEFAULT_MINUTES = MAX_MINUTES

#: The platform's magic key. Set, agent-runtimes' client sends it with every
#: runtime it creates, and Datalayer starts the runtime unmetered: it consumes
#: no credits and never expires. The demo only reads whether it is set.
MAGIC_API_KEY_ENV = "DATALAYER_MAGIC_API_KEY"

#: How long a new runtime has to answer before it is given back.
READY_TIMEOUT = 300.0

#: The runtime's A2A route for an application, under its agent-runtimes base.
A2A_ROUTE = "/api/v1/a2a/agents/{app}/"

#: The agentspec a new runtime starts with before the application replaces it
#: (agent-runtimes' ``BOOTSTRAP_AGENT_SPEC_ID``, what ``loop apps run`` starts with).
BOOTSTRAP_AGENT_SPEC_ID = "example-simple"

#: The agent a runtime holds, which the application is configured on.
RUNTIME_AGENT = "default"

NOT_SIGNED_IN = "Not signed in to Datalayer: run `datalayer login`, or set DATALAYER_API_KEY."
NEEDS_DEMO_EXTRA = (
    "The demo team needs agent-runtimes and agentspecs: `pip install 'agent-teams[demo]'`."
)


def unmetered() -> bool:
    """Whether the runtimes a deploy launches are unmetered: the magic key is set."""
    return bool(os.environ.get(MAGIC_API_KEY_ENV, "").strip())


def landing_setting(member_id: str) -> str:
    """The landing setting that takes a member's A2A address."""
    return f"demoTeam.{member_id}A2AUrl"


def runtime_name(team_id: str, member_id: str) -> str:
    """The name a member's demo runtime is given, and found again by."""
    return f"{RUNTIME_PREFIX}-{team_id}-{member_id}"


def a2a_address(base: str, app_id: str) -> str:
    """Where a runtime serves an application over A2A."""
    return base.rstrip("/") + A2A_ROUTE.format(app=app_id)


def card_address(address: str) -> str:
    """The agent card of an A2A address."""
    return f"{address.rstrip('/')}/.well-known/agent-card.json"


# ---------------------------------------------------------------------------
# The team, from the catalogue
# ---------------------------------------------------------------------------


@dataclass
class Member:
    """A member of the demo team, and the application it is."""

    id: str
    runs_in: str
    app_id: str = ""
    name: str = ""
    emoji: str = ""
    #: The Appspec, as its file holds it: what the runtime is configured with.
    document: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def hosted(self) -> bool:
        """Whether it runs on a runtime, which the deploy launches."""
        return self.runs_in == "runtime"

    @property
    def label(self) -> str:
        return f"{self.emoji} {self.name}".strip() or self.id


@dataclass
class DemoTeam:
    id: str
    name: str
    members: list[Member]

    @property
    def hosted(self) -> list[Member]:
        return [member for member in self.members if member.hosted]

    def member(self, member_id: str | None) -> Member:
        """A hosted member by id; the one there is when none is named."""
        hosted = self.hosted
        if member_id is None:
            if len(hosted) != 1:
                raise OrchestrationCommandError(
                    f"{self.name} has {len(hosted)} members on a runtime "
                    f"({', '.join(m.id for m in hosted)}): name one with --member."
                )
            return hosted[0]
        for member in hosted:
            if member.id == member_id:
                return member
        raise OrchestrationCommandError(
            f"{self.name} has no member '{member_id}' on a runtime "
            f"(on a runtime: {', '.join(m.id for m in hosted) or 'none'})."
        )


def _agentspecs() -> Any:
    try:
        import agentspecs.apps as apps
        import agentspecs.teams as teams
    except ImportError as error:
        raise OrchestrationCommandError(NEEDS_DEMO_EXTRA) from error
    return teams, apps


def _id_of(ref: str) -> tuple[str, str]:
    identity, _, version = ref.partition(":")
    return identity, version


def read_team(team_id: str) -> DemoTeam:
    """The team spec and the Appspec of each member it hosts, from agentspecs."""
    teams, apps = _agentspecs()
    spec = teams.get_team(team_id)
    if spec is None:
        raise OrchestrationCommandError(f"agentspecs has no team '{team_id}'.")
    documents = apps.load_raw_apps()
    members: list[Member] = []
    for agent in spec.agents:
        place = getattr(agent.runs_in, "value", agent.runs_in) or ""
        member = Member(id=agent.id, runs_in=str(place), name=agent.name or agent.id)
        if member.hosted:
            if not agent.app:
                raise OrchestrationCommandError(
                    f"{spec.name}: {agent.id} runs on a runtime and names no application."
                )
            identity, version = _id_of(agent.app)
            document = documents.get(identity)
            if document is None:
                raise OrchestrationCommandError(
                    f"{spec.name}: {agent.id} is the application '{identity}', "
                    "which agentspecs does not have."
                )
            if version and str(document.get("version")) != version:
                raise OrchestrationCommandError(
                    f"{spec.name}: {agent.id} is {agent.app}, and agentspecs has "
                    f"{identity}:{document.get('version')}."
                )
            member.app_id = identity
            member.name = agent.name or str(document.get("name") or agent.id)
            member.emoji = str(document.get("emoji") or "")
            member.document = document
        members.append(member)
    team = DemoTeam(id=spec.id, name=spec.name, members=members)
    if not team.hosted:
        raise OrchestrationCommandError(
            f"{spec.name} has no member that runs on a runtime: there is nothing to deploy."
        )
    return team


# ---------------------------------------------------------------------------
# Datalayer, as the signed-in person
# ---------------------------------------------------------------------------


def _launch() -> Any:
    try:
        from agent_runtimes.loop import launch
    except ImportError as error:
        raise OrchestrationCommandError(NEEDS_DEMO_EXTRA) from error
    return launch


class Cloud:
    """Datalayer as the signed-in person: agent-runtimes' client and launch helpers.

    The only part of the demo that reaches the platform; the commands are
    tested against a fake with the same methods.
    """

    def __init__(self) -> None:
        self._launch = _launch()
        try:
            self.client, self._token = self._launch.make_client()
        except self._launch.NotSignedIn:
            raise OrchestrationCommandError(NOT_SIGNED_IN) from None

    def whoami(self) -> str:
        """The person's handle, from IAM's whoami."""
        try:
            profile = self.client.get_profile()
        except Exception as error:
            raise OrchestrationCommandError(
                f"Datalayer did not say who these credentials are ({error}): "
                "run `datalayer login` again, or check DATALAYER_API_KEY."
            ) from None
        return str(profile.handle_s)

    def offer(self) -> Any:
        """The environments, the person's running runtimes and the credits left.

        The running runtimes are listed again through :meth:`running`, which
        fails the command when Datalayer does not list them. ``read_offer``
        reads a failed listing as nothing running, which here would plan a
        second runtime of the same name, and charge for it.
        """
        try:
            offer = self._launch.read_offer(self.client)
        except self._launch.CloudRefused as refused:
            raise OrchestrationCommandError(str(refused)) from None
        offer.running = self.running()
        return offer

    def running(self) -> list[Any]:
        """The person's running runtimes."""
        try:
            return list(self.client.list_runtimes())
        except Exception as error:
            raise OrchestrationCommandError(
                f"Datalayer did not list your runtimes: {error}"
            ) from None

    def environment(self, offer: Any, name: str | None) -> Any:
        """The environment a new runtime is reserved in: the one named, else the agent one."""
        try:
            return self._launch.choose_environment(
                offer.environments, environment=name, can_ask=False
            )
        except self._launch.CloudRefused as refused:
            raise OrchestrationCommandError(str(refused)) from None

    def cost(self, environment: Any, minutes: int) -> float:
        """The most a reservation can cost, in credits."""
        return float(self._launch.credits_for(environment, minutes))

    def create(self, name: str, environment: Any, minutes: int) -> Any:
        """Reserve a runtime, named, in an environment.

        With the magic key set, the runtime must come back unmetered: one that
        does not was launched by a Datalayer that does not take the key yet,
        and is stopped rather than left to charge.
        """
        try:
            runtime = self.client.create_runtime(
                name=name,
                environment=environment.name,
                time_reservation=minutes,
                agent_spec_id=BOOTSTRAP_AGENT_SPEC_ID,
            )
        except (RuntimeError, ValueError) as error:
            raise OrchestrationCommandError(f"Datalayer refused the launch: {error}") from None
        if unmetered() and not getattr(runtime, "unmetered", False):
            stopped = self.stop(str(runtime.uid))
            raise OrchestrationCommandError(
                f"{MAGIC_API_KEY_ENV} is set and Datalayer launched {runtime.uid} metered: "
                "its services do not take the magic key yet. "
                + (f"{runtime.uid} was stopped." if stopped else f"Stopping {runtime.uid} failed.")
            )
        return runtime

    def stop(self, uid: str) -> bool:
        """Stop a runtime; whether Datalayer said it stopped."""
        return bool(self.client.stop_runtime(uid))

    def base(self, runtime: Any) -> str:
        """A runtime's agent-runtimes address, as its callers reach it."""
        from agent_runtimes.client.agent_client import build_agent_runtimes_base_url

        return str(build_agent_runtimes_base_url(str(runtime.ingress)))

    def minutes_left(self, runtime: Any) -> int | None:
        return self._launch.minutes_left(getattr(runtime, "expired_at", None))

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        """A call to a runtime, as the person."""
        import httpx

        headers = {"Authorization": f"Bearer {self._token}"}
        return httpx.request(
            method, url, headers=headers, timeout=kwargs.pop("timeout", 30.0), **kwargs
        )


def connect() -> Cloud:
    """Datalayer as the signed-in person. The commands' one way in, so tests replace it."""
    return Cloud()


# ---------------------------------------------------------------------------
# What the commands do
# ---------------------------------------------------------------------------


def find_runtime(running: list[Any], name: str) -> Any | None:
    """The person's runtime of that name, if one is running."""
    found = [runtime for runtime in running if str(getattr(runtime, "name", "")) == name]
    if len(found) > 1:
        uids = ", ".join(str(runtime.uid) for runtime in found)
        raise OrchestrationCommandError(
            f"Several runtimes are named {name} ({uids}): "
            "`datalayer agent-teams demo stop` stops them all."
        )
    return found[0] if found else None


@dataclass
class Step:
    """What a deploy does for one member: reuse its runtime, or launch one."""

    member: Member
    name: str
    runtime: Any | None = None
    environment: Any | None = None
    minutes: int = 0
    cost: float = 0.0
    #: Launched on the magic key: no credits, no expiry.
    unmetered: bool = False

    @property
    def reuses(self) -> bool:
        return self.runtime is not None


def plan(
    cloud: Any,
    team: DemoTeam,
    *,
    environment: str | None,
    minutes: int,
) -> list[Step]:
    """Each hosted member's step, checked against the environments and the credits.

    With the magic key set (:func:`unmetered`), a launch costs nothing and no
    credit is asked; ``minutes`` is still validated and sent, and Datalayer
    ignores it for a runtime that never expires.
    """
    free = unmetered()
    offer = cloud.offer()
    steps: list[Step] = []
    for member in team.hosted:
        name = runtime_name(team.id, member.id)
        runtime = find_runtime(list(offer.running), name)
        if runtime is not None:
            steps.append(Step(member, name, runtime=runtime))
            continue
        chosen = cloud.environment(offer, environment)
        steps.append(
            Step(
                member,
                name,
                environment=chosen,
                minutes=minutes,
                cost=0.0 if free else cloud.cost(chosen, minutes),
                unmetered=free,
            )
        )
    launching = [step for step in steps if not step.reuses]
    if launching and not free:
        check_credits(offer.credits, sum(step.cost for step in launching), len(launching), minutes)
    return steps


def check_credits(left: float | None, cost: float, runtimes: int, minutes: int) -> None:
    """Refuse reservations the credits left cannot cover, or credits nobody knows."""
    if left is None:
        raise OrchestrationCommandError(
            "Datalayer did not say how many credits are left on your account, "
            "so no runtime is reserved: try again in a moment."
        )
    if left <= 0:
        raise OrchestrationCommandError(
            "No credits left on Datalayer: add credits to your account to deploy the demo team."
        )
    if cost > left:
        what = "a runtime" if runtimes == 1 else f"{runtimes} runtimes"
        raise OrchestrationCommandError(
            f"Reserving {what} for {minutes} min costs at most {cost:.2f} credits "
            f"and {left:.2f} are left: reserve fewer minutes (--minutes)."
        )


def configure_body(member: Member, base: str) -> dict[str, Any]:
    """What the runtime is configured with: the application, served over A2A, open to visitors."""
    return {"app": member.document, "a2a": True, "public_url": base, VISITORS_FIELD: True}


def wait_until_ready(
    cloud: Any,
    base: str,
    *,
    timeout: float = READY_TIMEOUT,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Whether the runtime's agent is up before the timeout: the one the application replaces."""
    deadline = clock() + timeout
    while clock() < deadline:
        try:
            response = cloud.request("GET", f"{base}/api/v1/agents", timeout=10.0)
            if response.status_code == 200:
                agents = response.json().get("agents") or []
                if any(agent.get("id") == RUNTIME_AGENT for agent in agents):
                    return True
        except Exception:  # noqa: S110 - not up yet; asked again until the deadline
            pass
        sleep(3.0)
    return False


def configure(cloud: Any, member: Member, base: str) -> dict[str, Any]:
    """Configure a runtime with a member's application; what it served, or the refusal in words."""
    response = cloud.request(
        "POST", f"{base}/api/v1/apps/configure", json=configure_body(member, base), timeout=120.0
    )
    if response.status_code == 422:
        detail = response.json().get("detail") or {}
        problems = detail.get("problems") if isinstance(detail, dict) else [str(detail)]
        raise OrchestrationCommandError(
            f"The runtime refused {member.label}: "
            + " ".join(problems or ["the application is not valid."])
        )
    if response.status_code >= 300:
        raise OrchestrationCommandError(
            f"The runtime did not take {member.label} ({response.status_code}): "
            f"{response.text[:300]}"
        )
    served = response.json().get("a2a") or {}
    if not served.get("url"):
        raise OrchestrationCommandError(
            f"The runtime took {member.label} and does not serve it over A2A: "
            "its agent-runtimes is older than the A2A route."
        )
    return served


def serving(cloud: Any, address: str) -> int | None:
    """The status the agent card answers with, or None when the runtime does not answer."""
    try:
        return int(cloud.request("GET", card_address(address), timeout=15.0).status_code)
    except Exception:
        return None


__all__ = [
    "DEMO_TEAM",
    "VISITORS_FIELD",
    "Cloud",
    "DemoTeam",
    "Member",
    "Step",
    "configure",
    "configure_body",
    "connect",
    "landing_setting",
    "plan",
    "read_team",
    "runtime_name",
]
