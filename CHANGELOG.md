<!--
  ~ Copyright (c) 2025-2026 Datalayer, Inc.
  ~
  ~ BSD 3-Clause License
-->

# Changelog

## Unreleased

- A team member may be a **coding agent** (SHIELD SH-45): `AgentMemberConfig.harness` names one of the catalogue (Claude Code, Codex, …), never with an endpoint, a spec, an MCP server, tools or a prompt (refused at load: the agent brings its own, the Shield decides them). `agent_teams.harness.harness_run_factory(workspace, policy=…, commands=…)` runs it behind `agent-shield run` over stdio, a session per task in the member's own folder, the agent's words its answer; a permission the Shield would ask a person for is refused (`reject`), nobody watching a team run. `TeamManager(harness_run_factory=…)`; a team with a harness member and no such runner is refused at start. The `shield` extra (`datalayer-agent-shield>=0.2.2,<0.3`). `tests/test_harness_member.py` (4) on a small ACP agent of the tests.
