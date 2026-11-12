# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Tests for agent_teams.cli module."""

from __future__ import annotations

import re

from typer.testing import CliRunner

from agent_teams.cli import app

runner = CliRunner()


def plain(text: str) -> str:
    """The text without its ANSI styling.

    Typer forces a terminal (colours, bold, dim) whenever GITHUB_ACTIONS is
    set, so on CI `--port` arrives as `\\x1b[1;36m--port` and a plain
    substring check misses it.
    """
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class TestCLIHelp:
    def test_main_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "the catalogued teams run on Datalayer" in " ".join(plain(result.stdout).split())
        # The self-hosted server's teams are their own group now; `serve` stays at the top.
        assert "local" in plain(result.stdout) and "serve" in result.stdout

    def test_listed_in_a_host_by_one_short_line(self):
        # In `datalayer --help` the group is one line among fifty: the long
        # sentence is its own `--help`, the listing shows the short one.
        import typer

        host = typer.Typer(name="host")
        host.command("other")(lambda: None)
        host.add_typer(app)
        result = runner.invoke(host, ["--help"], env={"COLUMNS": "120", "NO_COLOR": "1", "TERM": "dumb"})
        assert result.exit_code == 0
        listing = plain(result.stdout)
        assert "Run, follow and steer a team of agents." in listing
        assert "catalogued" not in listing
        assert len(app.info.short_help) <= 80
        own = plain(runner.invoke(host, ["agent-teams", "--help"]).stdout)
        assert "catalogued" in own

    def test_every_description_keeps_the_house_style(self):
        # One sentence ending with a period, a capital, short enough not to
        # wrap, not opening with the command's own name — as the Datalayer
        # CLI lists what this package contributes to it.
        import typer
        from reactor.cli import help_style_violations

        from agent_teams.reactor_extension import plugin

        host = typer.Typer(name="datalayer")
        host.command("other", help="Do the other thing.")(lambda: None)
        plugin()[1].provide_cli(host)
        assert help_style_violations(host) == []

    def test_serve_help(self):
        result = runner.invoke(app, ["serve", "--help"])
        assert result.exit_code == 0
        assert "--port" in plain(result.stdout)
        assert "--host" in plain(result.stdout)

    def test_create_help(self):
        result = runner.invoke(app, ["local", "create", "--help"])
        assert result.exit_code == 0
        assert "config_file" in plain(result.stdout)

    def test_list_help(self):
        result = runner.invoke(app, ["local", "list", "--help"])
        assert result.exit_code == 0

    def test_status_help(self):
        result = runner.invoke(app, ["local", "status", "--help"])
        assert result.exit_code == 0
        assert "team_id" in plain(result.stdout)

    def test_start_help(self):
        result = runner.invoke(app, ["local", "start", "--help"])
        assert result.exit_code == 0

    def test_stop_help(self):
        result = runner.invoke(app, ["local", "stop", "--help"])
        assert result.exit_code == 0

    def test_pause_help(self):
        result = runner.invoke(app, ["local", "pause", "--help"])
        assert result.exit_code == 0

    def test_resume_help(self):
        result = runner.invoke(app, ["local", "resume", "--help"])
        assert result.exit_code == 0

    def test_delete_help(self):
        result = runner.invoke(app, ["local", "delete", "--help"])
        assert result.exit_code == 0
        assert "--force" in plain(result.stdout)

    def test_assign_help(self):
        result = runner.invoke(app, ["local", "assign", "--help"])
        assert result.exit_code == 0
        assert "--priority" in plain(result.stdout)

    def test_metrics_help(self):
        result = runner.invoke(app, ["local", "metrics", "--help"])
        assert result.exit_code == 0

    def test_events_help(self):
        result = runner.invoke(app, ["local", "events", "--help"])
        assert result.exit_code == 0
        assert "--limit" in plain(result.stdout)


class TestCLICreateMissingFile:
    def test_create_nonexistent_file(self):
        result = runner.invoke(app, ["local", "create", "/nonexistent/path.json"])
        assert result.exit_code == 1
        assert "not found" in plain(result.output)
