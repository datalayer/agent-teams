<!--
  ~ Copyright (c) 2025-2026 Datalayer, Inc.
  ~
  ~ BSD 3-Clause License
-->

# Changelog

## Unreleased

- **A team run is a session** (MH-22, MH-23, MH-41): one run of a team owns one session per seat, opened at the seat's first task and kept until the run ends; `agent-teams sessions list|show|resume|swap|clone|export|import|end` act on it with the same names as `loop sessions …` (`--local` for a self-hosted server's files), the run's state read from the execution's and the seats' sessions from the record; `agent-teams status` gains each seat's session line; `docs/docs/sessions/` documents each verb with its refusal sentences. A harness member keeps one ACP session for the run (`HarnessRunner`), loaded again with `session/load` in another process when the agent advertises it, the start-over said otherwise.
- **A team run is extended through the reactor** (MH-F07): `teams.member.runner` (one contribution per member kind — `model`, `harness`, `a2a`) and `teams.session.orchestration` (`sequential`, `parallel`, `supervisor`; `graph` is refused in a sentence, no longer served by `supervisor` in its place). `TeamManager(platform=…)` reads both from a reactor platform (`agent_teams.plugins.teams_platform`); the constructor's `member_run_factory` and `harness_run_factory` and `set_member_run_factory` are gone; `create_app()`'s stub is a plugin. The orchestration owns the run's seat sessions and fires `session-open|resume|swap|clone|export|import|end` on the team's `HookRegistry`; a hook that disallows refuses the operation in its sentence; `post-team-stop` ends the seats' sessions.
- The sequential runner answers a task's last failure in its sentence, not *Exhausted all retries*.
- `datalayer_reactor` is a dependency; the `agent-teams` extension is advertised under `datalayer.reactor.extensions`.

- A team member may be a **coding agent** (SHIELD SH-45): `AgentMemberConfig.harness` names one of the catalogue (Claude Code, Codex, …), never with an endpoint, a spec, an MCP server, tools or a prompt (refused at load: the agent brings its own, the Shield decides them). `agent_teams.harness.harness_run_factory(workspace, policy=…, commands=…)` runs it behind `agent-shield run` over stdio, a session per task in the member's own folder, the agent's words its answer; a permission the Shield would ask a person for is refused (`reject`), nobody watching a team run. `TeamManager(harness_run_factory=…)`; a team with a harness member and no such runner is refused at start. The `shield` extra (`datalayer-agent-shield>=0.2.2,<0.3`). `tests/test_harness_member.py` (4) on a small ACP agent of the tests.
