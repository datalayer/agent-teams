# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Tests for agent_teams.cli module."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from agent_teams.cli import app

runner = CliRunner()


class TestCLIHelp:
    def test_main_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Manage and orchestrate AI agent teams" in result.stdout

    def test_serve_help(self):
        result = runner.invoke(app, ["serve", "--help"])
        assert result.exit_code == 0
        assert "--port" in result.stdout
        assert "--host" in result.stdout

    def test_create_help(self):
        result = runner.invoke(app, ["create", "--help"])
        assert result.exit_code == 0
        assert "CONFIG_FILE" in result.stdout

    def test_list_help(self):
        result = runner.invoke(app, ["list", "--help"])
        assert result.exit_code == 0

    def test_status_help(self):
        result = runner.invoke(app, ["status", "--help"])
        assert result.exit_code == 0
        assert "TEAM_ID" in result.stdout

    def test_start_help(self):
        result = runner.invoke(app, ["start", "--help"])
        assert result.exit_code == 0

    def test_stop_help(self):
        result = runner.invoke(app, ["stop", "--help"])
        assert result.exit_code == 0

    def test_pause_help(self):
        result = runner.invoke(app, ["pause", "--help"])
        assert result.exit_code == 0

    def test_resume_help(self):
        result = runner.invoke(app, ["resume", "--help"])
        assert result.exit_code == 0

    def test_delete_help(self):
        result = runner.invoke(app, ["delete", "--help"])
        assert result.exit_code == 0
        assert "--force" in result.stdout

    def test_assign_help(self):
        result = runner.invoke(app, ["assign", "--help"])
        assert result.exit_code == 0
        assert "--priority" in result.stdout

    def test_metrics_help(self):
        result = runner.invoke(app, ["metrics", "--help"])
        assert result.exit_code == 0

    def test_events_help(self):
        result = runner.invoke(app, ["events", "--help"])
        assert result.exit_code == 0
        assert "--limit" in result.stdout


class TestCLICreateMissingFile:
    def test_create_nonexistent_file(self):
        result = runner.invoke(app, ["create", "/nonexistent/path.json"])
        assert result.exit_code == 1
        assert "not found" in result.output
