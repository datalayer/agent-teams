# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""`agent-teams demo`: the landing's demo team, deployed under the person's account.

Nothing here reaches Datalayer: the platform is a fake with the methods of
`agent_teams.demo.Cloud`, answering with responses recorded from a runtime
(`POST /api/v1/apps/configure` with `a2a: true`), and the team and its
applications are agentspecs' as they are, or a copy of them when agentspecs is
not installed. What is held: a deploy launches only the members that run on a
runtime, names the runtime so the next deploy reuses it, configures it with
the application over A2A and open to visitors, and prints the landing setting;
it refuses, before launching anything, when nobody is signed in or the
credits cannot cover it; and a runtime it launched is given back when it does
not come up or does not take the application.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("datalayer_core", reason="the demo commands need agent-teams[datalayer]")

from typer.testing import CliRunner

from agent_teams import demo
from agent_teams.cli import app

runner = CliRunner(
    env={"NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200", "_TYPER_STANDARD_TRACEBACK": "1"}
)

UID = "01k6x2v9q8m3n4p5r6s7t8v9w0"
INGRESS = f"https://prod1.datalayer.run/jupyter/server/pool-ai-agents/{UID}"
BASE = f"https://prod1.datalayer.run/agent-runtimes/pool-ai-agents/{UID}"
ADDRESS = f"{BASE}/api/v1/a2a/agents/accounting/"
NAME = "agent-teams-demo-sales-and-accounting-accounting"

#: The Appspec agentspecs holds for Accounting, in short.
ACCOUNTING = {
    "schema": "loop.app/v1",
    "id": "accounting",
    "version": "0.0.1",
    "name": "Accounting",
    "kind": "chat",
    "agent": "worker-accountant:0.0.1",
    "connections": [{"server": "odoo-accounting:0.0.1", "access": "read", "as": "owner"}],
    "rules": [
        {"action": "Read the books", "applies_to": "read", "behaviour": "do_it"},
        {"action": "Change the books", "applies_to": ["write", "delete"], "behaviour": "ask_first"},
    ],
    "emoji": "🧾",
}

#: What a runtime answered to `POST /api/v1/apps/configure` with `a2a: true`.
CONFIGURED = {
    "app": {"id": "accounting", "version": "0.0.1", "name": "Accounting", "emoji": "🧾"},
    "setup": [],
    "a2a": {
        "url": ADDRESS,
        "card": f"{ADDRESS}.well-known/agent-card.json",
        "task": f"a2a:{UID}:accounting:",
    },
}


def plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def a_team() -> Any:
    """agentspecs' `sales-and-accounting`, as `agentspecs.teams.get_team` answers it."""
    return SimpleNamespace(
        id="sales-and-accounting",
        name="Sales and Accounting",
        agents=[
            SimpleNamespace(id="sales", app="sales:0.0.1", runs_in="browser", name=""),
            SimpleNamespace(id="accounting", app="accounting:0.0.1", runs_in="runtime", name=""),
        ],
    )


@pytest.fixture(autouse=True)
def the_specs(monkeypatch: pytest.MonkeyPatch) -> None:
    teams = SimpleNamespace(
        get_team=lambda team_id: a_team() if team_id == "sales-and-accounting" else None
    )
    apps = SimpleNamespace(load_raw_apps=lambda: {"accounting": dict(ACCOUNTING)})
    monkeypatch.setattr(demo, "_agentspecs", lambda: (teams, apps))


@pytest.fixture(autouse=True)
def no_magic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every launch is metered unless a test sets the platform's magic key."""
    monkeypatch.delenv(demo.MAGIC_API_KEY_ENV, raising=False)


class Runtime(SimpleNamespace):
    pass


def a_runtime(name: str = NAME, uid: str = UID) -> Runtime:
    return Runtime(
        uid=uid,
        name=name,
        ingress=INGRESS.replace(UID, uid),
        environment="ai-agents-env",
        expired_at="2026-10-05T18:00:00Z",
    )


ENVIRONMENT = SimpleNamespace(name="ai-agents-env", burning_rate=0.01)


class FakeCloud:
    """Datalayer as `agent_teams.demo.Cloud` reaches it, with recorded answers."""

    def __init__(
        self,
        *,
        running: list[Any] | None = None,
        left: float | None = 1000.0,
        agent_up: bool = True,
        configure: httpx.Response | None = None,
        card: int | None = 200,
    ) -> None:
        self.running_now = list(running or [])
        self.credits = left
        self.agent_up = agent_up
        self.configure_response = configure or httpx.Response(200, json=CONFIGURED)
        self.card = card
        self.created: list[tuple[str, str, int]] = []
        self.stopped: list[str] = []
        self.requests: list[tuple[str, str, Any]] = []

    def whoami(self) -> str:
        return "eric"

    def offer(self) -> Any:
        return SimpleNamespace(
            environments=[ENVIRONMENT], running=self.running_now, credits=self.credits
        )

    def running(self) -> list[Any]:
        return self.running_now

    def environment(self, offer: Any, name: str | None) -> Any:
        if name not in (None, ENVIRONMENT.name):
            raise demo.OrchestrationCommandError(f"There is no environment named “{name}”.")
        return ENVIRONMENT

    def cost(self, environment: Any, minutes: int) -> float:
        return environment.burning_rate * 60.0 * minutes

    def create(self, name: str, environment: Any, minutes: int) -> Any:
        self.created.append((name, environment.name, minutes))
        runtime = a_runtime(name)
        self.running_now.append(runtime)
        return runtime

    def stop(self, uid: str) -> bool:
        self.stopped.append(uid)
        return True

    def base(self, runtime: Any) -> str:
        return runtime.ingress.replace("/jupyter/server/", "/agent-runtimes/")

    def minutes_left(self, runtime: Any) -> int:
        return 472

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.requests.append((method, url, kwargs.get("json")))
        if url.endswith("/api/v1/agents"):
            agents = [{"id": "default", "transport": "vercel-ai"}] if self.agent_up else []
            return httpx.Response(200, json={"agents": agents})
        if url.endswith("/api/v1/apps/configure"):
            return self.configure_response
        if url.endswith("/.well-known/agent-card.json"):
            if self.card is None:
                raise httpx.ConnectError("no answer")
            return httpx.Response(self.card, json={"name": "Accounting"})
        return httpx.Response(404)

    def configured(self) -> list[Any]:
        return [body for method, url, body in self.requests if url.endswith("/apps/configure")]


@pytest.fixture
def cloud(monkeypatch: pytest.MonkeyPatch) -> FakeCloud:
    fake = FakeCloud()
    monkeypatch.setattr(demo, "connect", lambda: fake)
    return fake


def use(monkeypatch: pytest.MonkeyPatch, fake: FakeCloud) -> FakeCloud:
    monkeypatch.setattr(demo, "connect", lambda: fake)
    return fake


def invoke(*args: str) -> Any:
    return runner.invoke(app, ["demo", *args])


class TestTheGroup:
    def test_it_offers_deploy_status_url_and_stop(self) -> None:
        result = invoke("--help")
        assert result.exit_code == 0
        text = plain(result.output)
        for command in ("deploy", "status", "url", "stop"):
            assert command in text

    def test_deploy_says_its_options(self) -> None:
        text = plain(invoke("deploy", "--help").output)
        for option in ("--dry-run", "--environment", "--minutes", "--team", "--output"):
            assert option in text

    def test_the_visitors_field_is_one_constant(self) -> None:
        member = demo.read_team(demo.DEMO_TEAM).member(None)
        body = demo.configure_body(member, BASE)
        assert demo.VISITORS_FIELD == "visitors"
        assert body == {"app": ACCOUNTING, "a2a": True, "public_url": BASE, "visitors": True}


class TestTheTeam:
    def test_only_the_members_on_a_runtime_are_hosted(self) -> None:
        team = demo.read_team("sales-and-accounting")
        assert [m.id for m in team.members] == ["sales", "accounting"]
        assert [m.id for m in team.hosted] == ["accounting"]
        accounting = team.hosted[0]
        assert accounting.app_id == "accounting"
        assert accounting.document["agent"] == "worker-accountant:0.0.1"
        assert accounting.label == "🧾 Accounting"

    def test_an_unknown_team_is_refused(self) -> None:
        result = invoke("deploy", "--team", "nope", "--dry-run")
        assert result.exit_code == 1
        assert "agentspecs has no team 'nope'" in result.output

    def test_an_application_of_another_version_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        teams = SimpleNamespace(get_team=lambda team_id: a_team())
        older = {**ACCOUNTING, "version": "0.0.2"}
        apps = SimpleNamespace(load_raw_apps=lambda: {"accounting": older})
        monkeypatch.setattr(demo, "_agentspecs", lambda: (teams, apps))
        with pytest.raises(
            demo.OrchestrationCommandError, match=r"agentspecs has accounting:0\.0\.2"
        ):
            demo.read_team("sales-and-accounting")

    def test_landing_setting_and_runtime_name(self) -> None:
        assert demo.landing_setting("accounting") == "demoTeam.accountingA2AUrl"
        assert demo.runtime_name("sales-and-accounting", "accounting") == NAME


class TestDeploy:
    def test_a_dry_run_launches_and_configures_nothing(self, cloud: FakeCloud) -> None:
        result = invoke("deploy", "--dry-run")
        assert result.exit_code == 0, result.output
        text = plain(result.output)
        assert "deploying as eric (dry run)" in text
        assert f"launching {NAME} in ai-agents-env for 480 min (at most 288.00 credits)" in text
        assert "/api/v1/apps/configure with accounting:0.0.1, a2a: true" in text
        assert "visitors: true" in text
        assert "Dry run: nothing was launched or configured." in text
        assert cloud.created == []
        assert cloud.requests == []

    def test_a_dry_run_in_json(self, cloud: FakeCloud) -> None:
        result = invoke("deploy", "--dry-run", "--minutes", "60", "-o", "json")
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["account"] == "eric"
        assert payload["dryRun"] is True
        [row] = payload["members"]
        assert row["runtimeName"] == NAME
        assert row["environment"] == "ai-agents-env"
        assert row["minutes"] == 60
        assert row["maxCredits"] == 36.0
        assert row["setting"] == "demoTeam.accountingA2AUrl"

    def test_it_launches_configures_and_prints_the_setting(self, cloud: FakeCloud) -> None:
        result = invoke("deploy")
        assert result.exit_code == 0, result.output
        assert cloud.created == [(NAME, "ai-agents-env", 480)]
        [body] = cloud.configured()
        assert body["app"] == ACCOUNTING
        assert body["a2a"] is True
        assert body["public_url"] == BASE
        assert body[demo.VISITORS_FIELD] is True
        text = plain(result.output)
        assert f"A2A:  {ADDRESS}" in text
        assert f"Card: {ADDRESS}.well-known/agent-card.json" in text
        assert f"demoTeam.accountingA2AUrl = {ADDRESS}" in text
        assert cloud.stopped == []

    def test_a_second_deploy_reuses_the_runtime(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = use(monkeypatch, FakeCloud(running=[a_runtime(), a_runtime("other", "01kother")]))
        result = invoke("deploy")
        assert result.exit_code == 0, result.output
        assert fake.created == []
        assert f"reusing runtime {UID} (472 min left)" in plain(result.output)
        assert len(fake.configured()) == 1
        assert f"demoTeam.accountingA2AUrl = {ADDRESS}" in plain(result.output)

    def test_reusing_needs_no_credits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = use(monkeypatch, FakeCloud(running=[a_runtime()], left=0.0))
        assert invoke("deploy").exit_code == 0
        assert fake.created == []

    @pytest.mark.parametrize(
        ("left", "said"),
        [
            (None, "did not say how many credits are left"),
            (0.0, "No credits left on Datalayer"),
            (10.0, "costs at most 288.00 credits and 10.00 are left"),
        ],
    )
    def test_credits_that_cannot_cover_it_launch_nothing(
        self, monkeypatch: pytest.MonkeyPatch, left: float | None, said: str
    ) -> None:
        fake = use(monkeypatch, FakeCloud(left=left))
        result = invoke("deploy")
        assert result.exit_code == 1
        assert said in plain(result.output)
        assert fake.created == []

    def test_a_runtime_that_does_not_come_up_is_given_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = use(monkeypatch, FakeCloud(agent_up=False))
        monkeypatch.setattr(demo, "wait_until_ready", lambda cloud, base: False)
        result = invoke("deploy")
        assert result.exit_code == 1
        assert f"The runtime {UID} did not come up in time." in plain(result.output)
        assert f"The runtime {UID} was stopped." in plain(result.output)
        assert fake.stopped == [UID]
        assert fake.configured() == []

    def test_an_application_the_runtime_refuses_gives_the_runtime_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        refused = httpx.Response(
            422, json={"detail": {"problems": ["odoo-accounting is not in the catalogue."]}}
        )
        fake = use(monkeypatch, FakeCloud(configure=refused))
        result = invoke("deploy")
        assert result.exit_code == 1
        text = plain(result.output)
        assert "refused 🧾 Accounting: odoo-accounting is not in the catalogue." in text
        assert fake.stopped == [UID]

    def test_a_reused_runtime_is_not_stopped_when_it_refuses(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = use(
            monkeypatch,
            FakeCloud(running=[a_runtime()], configure=httpx.Response(500, text="boom")),
        )
        result = invoke("deploy")
        assert result.exit_code == 1
        assert "did not take 🧾 Accounting (500): boom" in plain(result.output)
        assert fake.stopped == []

    def test_a_runtime_without_the_a2a_route_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        answered = httpx.Response(200, json={**CONFIGURED, "a2a": None})
        fake = use(monkeypatch, FakeCloud(configure=answered))
        result = invoke("deploy")
        assert result.exit_code == 1
        assert "does not serve it over A2A" in plain(result.output)
        assert fake.stopped == [UID]

    def test_it_waits_for_the_runtime_agent(self) -> None:
        now = [0.0]
        fake = FakeCloud(agent_up=False)

        def sleep(seconds: float) -> None:
            now[0] += seconds
            if now[0] > 6:
                fake.agent_up = True

        assert demo.wait_until_ready(fake, BASE, sleep=sleep, clock=lambda: now[0])
        assert now[0] == 9.0

    def test_it_gives_up_at_the_deadline(self) -> None:
        now = [0.0]

        def sleep(seconds: float) -> None:
            now[0] += seconds

        assert not demo.wait_until_ready(
            FakeCloud(agent_up=False), BASE, timeout=30.0, sleep=sleep, clock=lambda: now[0]
        )


class TestStatusUrlStop:
    def test_status_of_a_deployed_team(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use(monkeypatch, FakeCloud(running=[a_runtime()]))
        result = invoke("status", "-o", "json")
        assert result.exit_code == 0, result.output
        sales, accounting = json.loads(result.output)["members"]
        assert sales == {
            "member": "sales",
            "runsIn": "browser",
            "state": "in the visitor's browser",
            "a2a": None,
            "runtime": None,
            "unmetered": False,
            "minutesLeft": None,
        }
        assert accounting == {
            "member": "accounting",
            "runsIn": "runtime",
            "state": "serving",
            "a2a": ADDRESS,
            "runtime": UID,
            "unmetered": False,
            "minutesLeft": 472,
        }

    def test_status_as_a_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use(monkeypatch, FakeCloud(running=[a_runtime()], card=404))
        text = plain(invoke("status").output)
        assert "not serving (HTTP 404)" in text
        assert "472 min" in text

    def test_status_of_a_team_not_deployed(self, cloud: FakeCloud) -> None:
        result = invoke("status", "-o", "json")
        assert json.loads(result.output)["members"][1]["state"] == "not deployed"

    def test_url_prints_only_the_address(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use(monkeypatch, FakeCloud(running=[a_runtime()]))
        result = invoke("url")
        assert result.exit_code == 0
        assert result.output == f"{ADDRESS}\n"

    def test_url_fails_when_not_deployed(self, cloud: FakeCloud) -> None:
        result = invoke("url")
        assert result.exit_code == 1
        assert "is not deployed" in plain(result.output)

    def test_url_fails_when_not_serving(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use(monkeypatch, FakeCloud(running=[a_runtime()], card=None))
        result = invoke("url")
        assert result.exit_code == 1
        assert "is not serving (no answer)" in plain(result.output)

    def test_url_of_a_browser_member_is_refused(self, cloud: FakeCloud) -> None:
        result = invoke("url", "--member", "sales")
        assert result.exit_code == 1
        assert "has no member 'sales' on a runtime" in plain(result.output)

    def test_stop_stops_the_demo_runtime_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = use(monkeypatch, FakeCloud(running=[a_runtime(), a_runtime("other", "01kother")]))
        result = invoke("stop")
        assert result.exit_code == 0, result.output
        assert fake.stopped == [UID]
        assert f"Stopped {UID}" in plain(result.output)

    def test_stop_with_nothing_running(self, cloud: FakeCloud) -> None:
        result = invoke("stop")
        assert result.exit_code == 0
        assert "no demo runtime is running" in plain(result.output)
        assert cloud.stopped == []


class TestTheCloud:
    """`Cloud` itself, on a fake of agent-runtimes' launch module."""

    def launch(self, make_client: Any) -> Any:
        class NotSignedIn(Exception):  # noqa: N818 - agent-runtimes' name
            pass

        class CloudRefused(Exception):  # noqa: N818 - agent-runtimes' name
            pass

        return SimpleNamespace(
            make_client=lambda: make_client(NotSignedIn),
            NotSignedIn=NotSignedIn,
            CloudRefused=CloudRefused,
        )

    def test_nobody_signed_in_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def make_client(not_signed_in: Any) -> Any:
            raise not_signed_in("No Datalayer credentials were found.")

        monkeypatch.setattr(demo, "_launch", lambda: self.launch(make_client))
        result = invoke("deploy", "--dry-run")
        assert result.exit_code == 1
        assert "Not signed in to Datalayer: run `datalayer login`" in plain(result.output)

    def test_credentials_iam_does_not_know_are_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def get_profile() -> Any:
            raise RuntimeError("Failed to get profile information")

        client = SimpleNamespace(get_profile=get_profile)
        monkeypatch.setattr(demo, "_launch", lambda: self.launch(lambda _: (client, "t")))
        result = invoke("deploy", "--dry-run")
        assert result.exit_code == 1
        assert "Datalayer did not say who these credentials are" in plain(result.output)

    def test_the_runtime_is_named_and_reached_as_the_person(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        created: dict[str, Any] = {}

        def create_runtime(**kwargs: Any) -> Any:
            created.update(kwargs)
            return a_runtime()

        client = SimpleNamespace(create_runtime=create_runtime)
        monkeypatch.setattr(demo, "_launch", lambda: self.launch(lambda _: (client, "tok")))
        sent: dict[str, Any] = {}

        def request(method: str, url: str, **kwargs: Any) -> httpx.Response:
            sent.update(method=method, url=url, **kwargs)
            return httpx.Response(200)

        monkeypatch.setattr(httpx, "request", request)
        the_cloud = demo.Cloud()
        the_cloud.create(NAME, ENVIRONMENT, 60)
        assert created == {
            "name": NAME,
            "environment": "ai-agents-env",
            "time_reservation": 60,
            "agent_spec_id": demo.BOOTSTRAP_AGENT_SPEC_ID,
        }
        the_cloud.request("GET", f"{BASE}/api/v1/agents")
        assert sent["headers"] == {"Authorization": "Bearer tok"}

    def test_a_refused_launch_is_said(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def create_runtime(**kwargs: Any) -> Any:
            raise RuntimeError("Runtime creation failed (environment='ai-agents-env'): quota")

        client = SimpleNamespace(create_runtime=create_runtime)
        monkeypatch.setattr(demo, "_launch", lambda: self.launch(lambda _: (client, "tok")))
        with pytest.raises(demo.OrchestrationCommandError, match="Datalayer refused the launch"):
            demo.Cloud().create(NAME, ENVIRONMENT, 60)


class TestTheOfferListsWhatRuns:
    """A listing that fails fails the command; it never reads as nothing deployed."""

    def a_cloud(self, monkeypatch: pytest.MonkeyPatch, list_runtimes: Any) -> Any:
        class CloudRefused(Exception):  # noqa: N818 - agent-runtimes' name
            pass

        def read_offer(client: Any) -> Any:
            # agent-runtimes' read_offer: a failed listing reads as nothing running.
            try:
                running = list(client.list_runtimes())
            except Exception:
                running = []
            return SimpleNamespace(environments=[ENVIRONMENT], running=running, credits=1000.0)

        launch = SimpleNamespace(
            make_client=lambda: (SimpleNamespace(list_runtimes=list_runtimes), "tok"),
            NotSignedIn=type("NotSignedIn", (Exception,), {}),
            CloudRefused=CloudRefused,
            read_offer=read_offer,
        )
        monkeypatch.setattr(demo, "_launch", lambda: launch)
        return demo.Cloud()

    def test_a_listing_that_fails_plans_no_launch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def list_runtimes() -> list[Any]:
            raise RuntimeError("502 Bad Gateway")

        the_cloud = self.a_cloud(monkeypatch, list_runtimes)
        with pytest.raises(demo.OrchestrationCommandError, match="did not list your runtimes"):
            demo.plan(the_cloud, demo.read_team(demo.DEMO_TEAM), environment=None, minutes=60)

    def test_what_runs_is_reused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        the_cloud = self.a_cloud(monkeypatch, lambda: [a_runtime()])
        [step] = demo.plan(the_cloud, demo.read_team(demo.DEMO_TEAM), environment=None, minutes=60)
        assert step.reuses


class TestMinutes:
    @pytest.mark.parametrize("minutes", ("0", "481"))
    def test_outside_the_platform_range_is_refused(self, cloud: FakeCloud, minutes: str) -> None:
        result = invoke("deploy", "--dry-run", "--minutes", minutes)
        assert result.exit_code == 2
        assert cloud.created == []

    def test_the_platform_maximum_is_taken(self, cloud: FakeCloud) -> None:
        assert invoke("deploy", "--dry-run", "--minutes", "480").exit_code == 0


class TestAnythingAfterTheLaunchGivesItBack:
    """Not only a refusal in words: a timeout, a refused connection, an answer that
    is not JSON — whatever fails once the runtime is launched stops it."""

    @pytest.mark.parametrize(
        "failure",
        (
            httpx.ReadTimeout("timed out"),
            httpx.ConnectError("refused"),
        ),
    )
    def test_a_transport_failure_while_configuring(
        self, monkeypatch: pytest.MonkeyPatch, failure: Exception
    ) -> None:
        fake = use(monkeypatch, FakeCloud())
        answer = fake.request

        def request(method: str, url: str, **kwargs: Any) -> httpx.Response:
            if url.endswith("/api/v1/apps/configure"):
                raise failure
            return answer(method, url, **kwargs)

        fake.request = request  # type: ignore[method-assign]
        result = invoke("deploy")
        assert result.exit_code == 1
        assert fake.stopped == [UID]
        text = plain(result.output)
        assert f"was not configured on {UID}" in text
        assert f"The runtime {UID} was stopped." in text

    def test_an_answer_that_is_not_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = use(monkeypatch, FakeCloud(configure=httpx.Response(200, text="<html>")))
        result = invoke("deploy")
        assert result.exit_code == 1
        assert fake.stopped == [UID]

    def test_a_runtime_whose_address_cannot_be_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = use(monkeypatch, FakeCloud())

        def base(runtime: Any) -> str:
            raise ValueError("no ingress")

        fake.base = base  # type: ignore[method-assign]
        result = invoke("deploy")
        assert result.exit_code == 1
        assert fake.stopped == [UID]

    def test_a_reused_runtime_is_left_running_and_the_failure_said(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = use(
            monkeypatch, FakeCloud(running=[a_runtime()], configure=httpx.Response(200, text="x"))
        )
        result = invoke("deploy")
        assert result.exit_code == 1
        assert fake.stopped == []
        assert f"was not configured on {UID}" in plain(result.output)


class TestUnmetered:
    """With DATALAYER_MAGIC_API_KEY set, a launch is free and never expires."""

    @pytest.fixture
    def magic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(demo.MAGIC_API_KEY_ENV, "the-magic-key-of-these-tests")

    def test_no_credit_is_asked(self, magic: None, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = use(monkeypatch, FakeCloud(left=0.0))
        result = invoke("deploy", "--dry-run")
        assert result.exit_code == 0, result.output
        text = plain(result.output)
        assert "unmetered (no credits, never expires)" in text
        assert "unmetered: yes" in text
        assert "the-magic-key-of-these-tests" not in text
        assert fake.created == []

    def test_a_dry_run_in_json_says_it(self, magic: None, cloud: FakeCloud) -> None:
        payload = json.loads(invoke("deploy", "--dry-run", "-o", "json").output)
        [row] = payload["members"]
        assert row["unmetered"] is True
        assert row["maxCredits"] == 0.0 and row["minutes"] is None
        assert "the-magic-key-of-these-tests" not in json.dumps(payload)

    def test_a_metered_dry_run_says_so(self, cloud: FakeCloud) -> None:
        assert "unmetered: no" in plain(invoke("deploy", "--dry-run").output)

    def test_status_says_never(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use(
            monkeypatch,
            FakeCloud(
                running=[Runtime(**{**vars(a_runtime()), "unmetered": True, "expired_at": ""})]
            ),
        )
        result = invoke("status")
        assert result.exit_code == 0, result.output
        assert "never" in plain(result.output)
        payload = json.loads(invoke("status", "-o", "json").output)
        accounting = next(row for row in payload["members"] if row["member"] == "accounting")
        assert accounting["unmetered"] is True and accounting["minutesLeft"] is None

    def test_a_runtime_launched_metered_is_stopped(
        self, magic: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Datalayer that does not take the key yet launches it metered: given back."""
        stopped: list[str] = []
        client = SimpleNamespace(
            create_runtime=lambda **kwargs: a_runtime(),
            stop_runtime=lambda uid: stopped.append(uid) or True,
        )
        launch = SimpleNamespace(
            make_client=lambda: (client, "tok"),
            NotSignedIn=type("NotSignedIn", (Exception,), {}),
        )
        monkeypatch.setattr(demo, "_launch", lambda: launch)
        with pytest.raises(demo.OrchestrationCommandError, match=r"launched .* metered"):
            demo.Cloud().create(NAME, ENVIRONMENT, 60)
        assert stopped == [UID]

    def test_a_runtime_launched_unmetered_is_kept(
        self, magic: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = SimpleNamespace(
            create_runtime=lambda **kwargs: Runtime(**{**vars(a_runtime()), "unmetered": True}),
        )
        launch = SimpleNamespace(
            make_client=lambda: (client, "tok"),
            NotSignedIn=type("NotSignedIn", (Exception,), {}),
        )
        monkeypatch.setattr(demo, "_launch", lambda: launch)
        assert demo.Cloud().create(NAME, ENVIRONMENT, 60).unmetered is True


def _has_agentspecs_team() -> bool:
    try:
        import agentspecs.teams as teams
    except ImportError:
        return False
    return teams.get_team(demo.DEMO_TEAM) is not None


@pytest.mark.skipif(
    not _has_agentspecs_team(), reason="needs agentspecs>=0.0.37 (agent-teams[demo])"
)
def test_the_catalogued_team_hosts_accounting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()
    team = demo.read_team(demo.DEMO_TEAM)
    [accounting] = team.hosted
    assert accounting.id == "accounting"
    assert accounting.document["agent"].startswith("worker-accountant")
    assert accounting.document["connections"][0]["server"].startswith("odoo-accounting")
    assert [m.runs_in for m in team.members] == ["browser", "runtime"]
