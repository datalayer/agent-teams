# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Tests for agent_teams.a2a.orchestration_extension.

The claim under test is round-trip fidelity: what an orchestrator's
``build_delegation_meta`` writes is exactly what a worker's
``read_delegation_meta`` reads back, and what a worker's ``usage_meta`` /
``paused_meta`` / ``error_meta`` write is exactly what an orchestrator's
``read_usage_meta`` reads back — over the plain ``dict`` shape an A2A
message's ``metadata`` (or an ACP prompt's ``_meta``) actually is, with no
control-plane type on either side of the round trip.
"""

from __future__ import annotations

from agent_teams.a2a.orchestration_extension import (
    BUDGET_FIELD,
    CHECKPOINT_FIELD,
    CREDENTIAL_FIELD,
    ENVELOPE_KEY,
    ERROR_FIELD,
    EXECUTION_FIELD,
    ORCHESTRATION_EXTENSION_URI,
    PAUSE_FIELD,
    PAUSED_FIELD,
    STEER_METHOD,
    USAGE_FIELD,
    Budget,
    ExecutionRef,
    Usage,
    agent_extension,
    build_delegation_meta,
    error_meta,
    is_extension_active,
    paused_meta,
    read_delegation_meta,
    read_usage_meta,
    steer_notification,
    usage_meta,
)


class TestConstants:
    def test_uri(self):
        assert ORCHESTRATION_EXTENSION_URI == "https://datalayer.ai/extensions/orchestration/v1"

    def test_the_envelope_key_is_the_one_key_the_extension_uses(self):
        # One key, not nine, so a plain worker's message is untouched by it
        # and a proxy can strip the whole extension in one step.
        assert ENVELOPE_KEY == "datalayer"

    def test_the_steer_method(self):
        assert STEER_METHOD == "_datalayer/steer"


class TestBuildDelegationMeta:
    def test_the_execution_is_always_present(self):
        execution = ExecutionRef(execution_id="exec_1", root_execution_id="exec_1", depth=0)
        meta = build_delegation_meta(execution)
        assert meta[ENVELOPE_KEY]["execution"] == {
            "executionId": "exec_1",
            "rootExecutionId": "exec_1",
            "depth": 0,
        }

    def test_a_child_names_its_parent(self):
        execution = ExecutionRef(
            execution_id="exec_2",
            root_execution_id="exec_1",
            depth=1,
            parent_execution_id="exec_1",
            account_uid="acc_1",
        )
        written = build_delegation_meta(execution)[ENVELOPE_KEY]["execution"]
        assert written["parentExecutionId"] == "exec_1"
        assert written["accountUid"] == "acc_1"

    def test_a_root_execution_names_neither(self):
        execution = ExecutionRef(execution_id="exec_1", root_execution_id="exec_1")
        written = build_delegation_meta(execution)[ENVELOPE_KEY]["execution"]
        assert "parentExecutionId" not in written
        assert "accountUid" not in written

    def test_a_budget_dataclass_and_a_plain_mapping_write_the_same_shape(self):
        execution = ExecutionRef(execution_id="exec_1", root_execution_id="exec_1")
        from_dataclass = build_delegation_meta(
            execution, budget=Budget(output_tokens=4000)
        )[ENVELOPE_KEY]["budget"]
        from_mapping = build_delegation_meta(
            execution, budget={"outputTokens": 4000, "currency": "USD"}
        )[ENVELOPE_KEY]["budget"]
        assert from_dataclass == from_mapping == {"outputTokens": 4000, "currency": "USD"}

    def test_no_budget_writes_no_budget_field(self):
        execution = ExecutionRef(execution_id="exec_1", root_execution_id="exec_1")
        assert "budget" not in build_delegation_meta(execution)[ENVELOPE_KEY]

    def test_a_credential_is_carried(self):
        execution = ExecutionRef(execution_id="exec_1", root_execution_id="exec_1")
        meta = build_delegation_meta(execution, credential="tok_secret")
        assert meta[ENVELOPE_KEY]["credential"] == "tok_secret"

    def test_no_credential_writes_no_credential_field(self):
        execution = ExecutionRef(execution_id="exec_1", root_execution_id="exec_1")
        assert "credential" not in build_delegation_meta(execution)[ENVELOPE_KEY]

    def test_a_checkpoint_names_where_to_resume_from(self):
        execution = ExecutionRef(execution_id="exec_1", root_execution_id="exec_1")
        meta = build_delegation_meta(execution, checkpoint_id="ckpt_1")
        assert meta[ENVELOPE_KEY]["checkpoint"] == {"checkpointId": "ckpt_1"}

    def test_pause_is_present_only_when_asked_for(self):
        execution = ExecutionRef(execution_id="exec_1", root_execution_id="exec_1")
        assert "pause" not in build_delegation_meta(execution)[ENVELOPE_KEY]
        assert build_delegation_meta(execution, pause=True)[ENVELOPE_KEY]["pause"] is True


class TestReadDelegationMeta:
    def test_nothing_sent_reads_as_nothing(self):
        delegation = read_delegation_meta({})
        assert delegation.execution is None
        assert delegation.budget is None
        assert delegation.credential is None
        assert delegation.checkpoint_id is None
        assert delegation.pause_requested is False

    def test_a_message_with_no_metadata_at_all_reads_as_nothing(self):
        # `metadata` can be `None` on a real A2A message.
        delegation = read_delegation_meta(None)
        assert delegation.execution is None

    def test_a_plain_worker_message_with_unrelated_metadata_is_untouched(self):
        delegation = read_delegation_meta({"some-other-key": "value"})
        assert delegation.execution is None

    def test_every_field_a_delegation_carries_reads_back(self):
        execution = ExecutionRef(
            execution_id="exec_2",
            root_execution_id="exec_1",
            depth=1,
            parent_execution_id="exec_1",
            account_uid="acc_1",
        )
        meta = build_delegation_meta(
            execution,
            budget=Budget(output_tokens=4000, cost=0.5),
            credential="tok_secret",
            checkpoint_id="ckpt_1",
            pause=True,
        )
        delegation = read_delegation_meta(meta)
        assert delegation.execution == execution
        assert delegation.budget["outputTokens"] == 4000
        assert delegation.budget["cost"] == 0.5
        assert delegation.credential == "tok_secret"
        assert delegation.checkpoint_id == "ckpt_1"
        assert delegation.pause_requested is True

    def test_a_root_executions_depth_defaults_when_absent(self):
        # A hand-built envelope from a third-party orchestrator that forgot
        # `depth` should not raise; it reads as a root.
        delegation = read_delegation_meta(
            {ENVELOPE_KEY: {"execution": {"executionId": "exec_1"}}}
        )
        assert delegation.execution.root_execution_id == "exec_1"
        assert delegation.execution.depth == 0


class TestUsage:
    def test_what_a_worker_reports_reads_back(self):
        meta = usage_meta(input_tokens=812, output_tokens=140, cost=0.0031)
        usage = read_usage_meta(meta)
        assert usage == Usage(input_tokens=812, output_tokens=140, cost=0.0031, currency="USD")

    def test_a_worker_that_could_not_price_its_model_reports_no_cost(self):
        meta = usage_meta(input_tokens=812, output_tokens=140)
        usage = read_usage_meta(meta)
        assert usage.cost is None
        assert "cost" not in meta[ENVELOPE_KEY]["usage"]

    def test_a_worker_that_says_nothing_reports_nothing(self):
        assert read_usage_meta({}) is None
        assert read_usage_meta(None) is None


class TestPausedAndError:
    def test_a_paused_worker_names_its_own_checkpoint(self):
        meta = paused_meta("ckpt_1")
        assert meta[ENVELOPE_KEY]["paused"] == {"checkpointId": "ckpt_1"}

    def test_a_budget_refusal_names_the_limit(self):
        meta = error_meta(code="budget_exhausted", limit="output_tokens")
        assert meta[ENVELOPE_KEY]["error"] == {
            "code": "budget_exhausted",
            "limit": "output_tokens",
        }

    def test_a_refusal_with_no_limit_names_only_the_code(self):
        meta = error_meta(code="worker_unreachable")
        assert meta[ENVELOPE_KEY]["error"] == {"code": "worker_unreachable"}


class TestNegotiation:
    def test_the_extension_is_active_when_advertised(self):
        assert is_extension_active([ORCHESTRATION_EXTENSION_URI])
        assert is_extension_active(["a", ORCHESTRATION_EXTENSION_URI, "b"])

    def test_the_extension_is_not_active_by_default(self):
        assert not is_extension_active(["some-other-extension"])
        assert not is_extension_active([])
        assert not is_extension_active(None)

    def test_the_agent_card_entry_names_the_uri(self):
        entry = agent_extension()
        assert entry["uri"] == ORCHESTRATION_EXTENSION_URI
        assert entry["required"] is False

    def test_a_required_extension_can_be_declared(self):
        assert agent_extension(required=True)["required"] is True


class TestSteer:
    def test_a_steer_notification_carries_no_id(self):
        # A notification, not a request: nothing answers it, so it has no
        # JSON-RPC `id` for an answer to correlate against.
        notification = steer_notification("sess-1", "check the assumptions")
        assert "id" not in notification
        assert notification["method"] == STEER_METHOD
        assert notification["params"] == {
            "sessionId": "sess-1",
            "instructions": "check the assumptions",
        }


class TestNothingHereImportsTheControlPlane:
    def test_the_module_imports_with_no_datalayer_platform_package(self):
        # The whole point of O3-02: this module must be usable by a worker
        # that has never heard of Datalayer's control plane. Asserted by
        # the module's actual `import`/`from ... import` statements — not a
        # substring match on its source, which would also catch the
        # cross-reference to `agent_runtimes.context.delegation` in its own
        # docstring, naming the sibling implementation this one is checked
        # against rather than depending on.
        import ast
        import inspect

        from agent_teams.a2a import orchestration_extension

        tree = ast.parse(inspect.getsource(orchestration_extension))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        forbidden = {"datalayer_core", "agent_runtimes", "datalayer_durable"}
        assert not (imported & forbidden), imported & forbidden
