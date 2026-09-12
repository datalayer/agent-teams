# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""The Datalayer orchestration extension, standalone (ORCHESTRATOR.md, O3-01, O3-02).

A worker built on ``fasta2a`` and an orchestrator dispatching to it can say
three things over plain A2A neither protocol has a field for: which
execution a delegation is part of and where it sits in its tree, a budget
the worker should decline before exceeding rather than have detected after
the spend, and — once the worker's task or session is named — a checkpoint
to resume from and an instruction to steer a run already in progress.

This module is that agreement, and nothing else: no control-plane import,
no store, no lifecycle. An orchestrator that never delegates a child sends
`execution` with `depth=0` and no parent; a worker that never checkpoints
never answers `paused`. Everything is optional and capability-negotiated —
call :func:`is_extension_active` before sending anything, and a worker that
never advertises the extension still gets a plain A2A delegation and still
answers.

The published, normative specification, with the schema this module's shape
is checked against, is at
``research/orchestration-protocols/docs/extension-v1.md`` in the Datalayer
monorepo (`datalayer/ui`) — this package is the extension's implementation,
not a copy of the spec, so a change to one is read against the other by the
tests that exercise a real delegation built from this module's own
functions (``test_orchestration_extension.py``).

Usage
-----
An orchestrator, dispatching::

    from agent_teams.a2a import ExecutionRef, build_delegation_meta

    meta = build_delegation_meta(
        ExecutionRef(execution_id="exec_1", root_execution_id="exec_1", depth=0),
        budget={"outputTokens": 4000},
        credential=token,
    )
    message = Message(role="user", parts=[Part(text=objective)], metadata=meta)

A worker, answering::

    from agent_teams.a2a import usage_meta

    message = Message(
        role="agent",
        parts=[Part(text=answer)],
        metadata=usage_meta(input_tokens=812, output_tokens=140, cost=0.0031),
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

__all__ = [
    "ORCHESTRATION_EXTENSION_URI",
    "ENVELOPE_KEY",
    "STEER_METHOD",
    "EXECUTION_FIELD",
    "BUDGET_FIELD",
    "CREDENTIAL_FIELD",
    "CHECKPOINT_FIELD",
    "PAUSE_FIELD",
    "USAGE_FIELD",
    "PAUSED_FIELD",
    "ERROR_FIELD",
    "ExecutionRef",
    "Budget",
    "Usage",
    "agent_extension",
    "extension_agent_extension",
    "build_delegation_meta",
    "read_delegation_meta",
    "usage_meta",
    "paused_meta",
    "error_meta",
    "read_usage_meta",
    "is_extension_active",
    "steer_notification",
]

#: The extension identifier, exactly as `agent_runtimes.context.delegation`
#: and the published specification name it. Stable across languages and
#: implementations: this is what an agent card advertises and what an ACP
#: `initialize` result names, so it is never derived, only imported.
ORCHESTRATION_EXTENSION_URI = "https://datalayer.ai/extensions/orchestration/v1"

#: Where the envelope lives in an A2A message's `metadata`. One key, not
#: nine, so a plain worker's message is untouched by it and a proxy can
#: strip the whole extension in one step.
ENVELOPE_KEY = "datalayer"

#: The one method the extension adds: instructions delivered to a turn
#: already in progress. A2A has no way to add instructions to a running
#: task; this is why the field exists, defined once so an orchestrator does
#: not branch on protocol between this and ACP's `session/prompt` again.
STEER_METHOD = "_datalayer/steer"

#: The field names inside the envelope, public so a caller that needs to
#: read or write one directly — rather than through the functions below —
#: is naming the same string this module does, not a private implementation
#: detail it copied. This is what `agent_runtimes.context.delegation` and
#: `agent_runtimes.guardrails.model_budget` import (ORCHESTRATOR.md, O3-02):
#: those modules keep their own runtime-specific state (held credentials,
#: the current run's `ContextVar`, pydantic-ai's `SteerCapability`), which
#: has no equivalent here and does not belong in a protocol-only package,
#: but the wire-level names are these, not a second declaration of them.
EXECUTION_FIELD = "execution"
BUDGET_FIELD = "budget"
CREDENTIAL_FIELD = "credential"
CHECKPOINT_FIELD = "checkpoint"
PAUSE_FIELD = "pause"
USAGE_FIELD = "usage"
PAUSED_FIELD = "paused"
ERROR_FIELD = "error"


@dataclass(frozen=True)
class ExecutionRef:
    """Which execution a delegation is part of, and where it sits in its tree.

    Neither A2A's ``contextId`` nor ACP's ``session/fork`` can say which task
    is whose parent or how deep a tree goes; this is the field that answers
    it. A root execution names itself as both ``execution_id`` and
    ``root_execution_id`` and carries no parent.

    Parameters
    ----------
    execution_id : str
        This execution's own identifier.
    root_execution_id : str
        The tree's root. Equal to ``execution_id`` for a root execution.
    depth : int
        How many delegations deep this execution is; 0 for a root.
    parent_execution_id : str | None
        The execution that delegated this one, when there is one.
    account_uid : str | None
        The account this execution belongs to — what a worker's own request
        for a child is made in, when it asks the orchestrator for one rather
        than delegating directly.
    """

    execution_id: str
    root_execution_id: str
    depth: int = 0
    parent_execution_id: str | None = None
    account_uid: str | None = None


@dataclass(frozen=True)
class Budget:
    """What a worker should decline before exceeding, in its own currency.

    Nothing about a budget is required: a worker told nothing about its
    budget behaves exactly as it would delegated over plain A2A, and an
    orchestrator that tracks no cost sends none of these fields.

    Parameters
    ----------
    input_tokens : int | None
        The input token ceiling, when there is one.
    output_tokens : int | None
        The output token ceiling, when there is one.
    cost : float | None
        A cost ceiling, when the orchestrator tracks one.
    currency : str
        What ``cost`` is denominated in.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: float | None = None
    currency: str = "USD"

    def to_wire(self) -> dict[str, Any]:
        """
        This budget, as the envelope's ``budget`` field.

        Returns
        -------
        dict[str, Any]
            Only the limits actually set; ``currency`` always, since a bare
            number with no unit is not a budget.
        """
        wire: dict[str, Any] = {"currency": self.currency}
        if self.input_tokens is not None:
            wire["inputTokens"] = self.input_tokens
        if self.output_tokens is not None:
            wire["outputTokens"] = self.output_tokens
        if self.cost is not None:
            wire["cost"] = self.cost
        return wire


@dataclass(frozen=True)
class Usage:
    """What one turn spent, as a worker answers it.

    Nothing else records this: a budget's ``cost`` is a limit, never a
    measurement, and an orchestrator managing a tree has no other way to
    know what one node of it actually used.

    Parameters
    ----------
    input_tokens : int | None
        Input tokens this turn spent.
    output_tokens : int | None
        Output tokens this turn spent.
    cost : float | None
        What this turn cost, when the worker could price its own model.
    currency : str
        What ``cost`` is denominated in.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: float | None = None
    currency: str = "USD"

    def to_wire(self) -> dict[str, Any]:
        """
        This usage, as the envelope's ``usage`` field.

        Returns
        -------
        dict[str, Any]
            Only what was actually reported.
        """
        wire: dict[str, Any] = {"currency": self.currency}
        if self.input_tokens is not None:
            wire["inputTokens"] = self.input_tokens
        if self.output_tokens is not None:
            wire["outputTokens"] = self.output_tokens
        if self.cost is not None:
            wire["cost"] = self.cost
        return wire


def _ours(metadata: Any) -> Mapping[str, Any] | None:
    """
    The extension's own object inside a message's metadata, if there is one.

    Parameters
    ----------
    metadata : Any
        An A2A message's ``metadata``, untrusted — it comes from a worker
        or an orchestrator on the other side of the wire.

    Returns
    -------
    Mapping[str, Any] | None
        The envelope, or ``None`` when nothing was sent under this key.
    """
    if not isinstance(metadata, Mapping):
        return None
    envelope = metadata.get(ENVELOPE_KEY)
    return envelope if isinstance(envelope, Mapping) else None


def is_extension_active(activated_extensions: list[str] | None) -> bool:
    """
    Whether the peer advertised or activated this extension.

    An A2A agent card lists the extensions it speaks in
    ``capabilities.extensions``; an A2A request may list the extensions the
    caller activated in ``extensions``. Either list is what this checks.
    Send nothing under :data:`ENVELOPE_KEY` to a peer this says ``False``
    for — a plain worker's message must stay untouched by it.

    Parameters
    ----------
    activated_extensions : list[str] | None
        The list of extension URIs from either side.

    Returns
    -------
    bool
        Whether :data:`ORCHESTRATION_EXTENSION_URI` is one of them.
    """
    return bool(activated_extensions) and ORCHESTRATION_EXTENSION_URI in activated_extensions


def agent_extension(*, required: bool = False) -> dict[str, Any]:
    """
    The ``AgentExtension`` entry for an agent card's ``capabilities.extensions``.

    Matches the shape ``agent_teams.a2a.extensions.agent_extension`` already
    uses for the team-coordination extension — the two are independent,
    separately negotiated extensions that can be advertised side by side on
    the same card.

    Parameters
    ----------
    required : bool
        Whether a caller must activate this extension to use the agent at
        all. False everywhere this ships: the whole point is that a plain
        A2A caller still works.

    Returns
    -------
    dict[str, Any]
        The extension descriptor.
    """
    return {
        "uri": ORCHESTRATION_EXTENSION_URI,
        "description": (
            "Datalayer orchestration: which execution a delegation is part "
            "of, a budget to decline before exceeding, a checkpoint to "
            "resume from, and instructions delivered to a turn already in "
            "progress."
        ),
        "required": required,
    }


# Kept as an alias under the name this module's own docstring and the a2a
# package's `__init__` use, so `from agent_teams.a2a import
# extension_agent_extension` and `agent_extension` never collide with the
# team-coordination extension's own `agent_extension` of the same name.
extension_agent_extension = agent_extension


def build_delegation_meta(
    execution: ExecutionRef,
    *,
    budget: Budget | Mapping[str, Any] | None = None,
    credential: str | None = None,
    checkpoint_id: str | None = None,
    pause: bool = False,
) -> dict[str, Any]:
    """
    What an orchestrator's delegation carries, under :data:`ENVELOPE_KEY`.

    Parameters
    ----------
    execution : ExecutionRef
        The execution this delegation is for.
    budget : Budget | Mapping[str, Any] | None
        The limits this worker should decline before exceeding.
    credential : str | None
        A bearer credential the worker may reach the caller's platform with
        for this run. Callers that hold one should take it out of a stored
        message once read, the same discipline
        ``agent_runtimes.context.delegation`` already follows — a credential
        left in task history is a credential anyone reading that history
        holds too.
    checkpoint_id : str | None
        Resume from this checkpoint, naming what a worker's own ``paused``
        answer returned. Requires the worker to have already reported one;
        sending it to a worker that never has is asking it to resume
        something it never held.
    pause : bool
        Ask the worker to stop at a checkpoint it can be resumed from later.

    Returns
    -------
    dict[str, Any]
        A ``metadata`` fragment: ``{"datalayer": {...}}``.
    """
    envelope: dict[str, Any] = {
        EXECUTION_FIELD: {
            "executionId": execution.execution_id,
            "rootExecutionId": execution.root_execution_id,
            "depth": execution.depth,
            **(
                {"parentExecutionId": execution.parent_execution_id}
                if execution.parent_execution_id
                else {}
            ),
            **({"accountUid": execution.account_uid} if execution.account_uid else {}),
        }
    }
    if budget is not None:
        envelope[BUDGET_FIELD] = budget.to_wire() if isinstance(budget, Budget) else dict(budget)
    if credential:
        envelope[CREDENTIAL_FIELD] = credential
    if checkpoint_id:
        envelope[CHECKPOINT_FIELD] = {"checkpointId": checkpoint_id}
    if pause:
        envelope[PAUSE_FIELD] = True
    return {ENVELOPE_KEY: envelope}


@dataclass(frozen=True)
class _Delegation:
    """
    What a worker read out of a delegation's metadata.

    Every field answers "was this sent", not "does this execution exist" —
    the metadata is untrusted, from the other side of the wire, and read as
    data rather than followed as an instruction.
    """

    execution: ExecutionRef | None = None
    budget: dict[str, Any] | None = None
    credential: str | None = None
    checkpoint_id: str | None = None
    pause_requested: bool = False


def read_delegation_meta(metadata: Any) -> _Delegation:
    """
    Read what an orchestrator's delegation carried, from a message's metadata.

    Parameters
    ----------
    metadata : Any
        An A2A message's ``metadata``, or an ACP prompt's ``_meta`` — the
        envelope's shape does not depend on which protocol carried it.

    Returns
    -------
    _Delegation
        What was actually sent; every field is ``None`` or ``False`` when
        nothing was.
    """
    envelope = _ours(metadata)
    if envelope is None:
        return _Delegation()
    execution_ref: ExecutionRef | None = None
    raw_execution = envelope.get(EXECUTION_FIELD)
    if isinstance(raw_execution, Mapping) and raw_execution.get("executionId"):
        execution_ref = ExecutionRef(
            execution_id=str(raw_execution["executionId"]),
            root_execution_id=str(raw_execution.get("rootExecutionId") or raw_execution["executionId"]),
            depth=int(raw_execution.get("depth") or 0),
            parent_execution_id=(
                str(raw_execution["parentExecutionId"])
                if raw_execution.get("parentExecutionId")
                else None
            ),
            account_uid=(
                str(raw_execution["accountUid"]) if raw_execution.get("accountUid") else None
            ),
        )
    raw_budget = envelope.get(BUDGET_FIELD)
    raw_checkpoint = envelope.get(CHECKPOINT_FIELD)
    return _Delegation(
        execution=execution_ref,
        budget=dict(raw_budget) if isinstance(raw_budget, Mapping) else None,
        credential=str(envelope[CREDENTIAL_FIELD]) if envelope.get(CREDENTIAL_FIELD) else None,
        checkpoint_id=(
            str(raw_checkpoint["checkpointId"])
            if isinstance(raw_checkpoint, Mapping) and raw_checkpoint.get("checkpointId")
            else None
        ),
        pause_requested=bool(envelope.get(PAUSE_FIELD)),
    )


def usage_meta(
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost: float | None = None,
    currency: str = "USD",
) -> dict[str, Any]:
    """
    What a worker's answer carries about what the turn spent.

    Parameters
    ----------
    input_tokens : int | None
        Input tokens this turn spent.
    output_tokens : int | None
        Output tokens this turn spent.
    cost : float | None
        What this turn cost, when the worker could price its own model.
    currency : str
        What ``cost`` is denominated in.

    Returns
    -------
    dict[str, Any]
        A ``metadata`` fragment naming what was spent.
    """
    usage = Usage(
        input_tokens=input_tokens, output_tokens=output_tokens, cost=cost, currency=currency
    )
    return {ENVELOPE_KEY: {USAGE_FIELD: usage.to_wire()}}


def paused_meta(checkpoint_id: str) -> dict[str, Any]:
    """
    What a worker answers a ``pause`` request with.

    Parameters
    ----------
    checkpoint_id : str
        The checkpoint the worker actually stopped at — its own identifier,
        not necessarily one the orchestrator named.

    Returns
    -------
    dict[str, Any]
        A ``metadata`` fragment naming the checkpoint.
    """
    return {ENVELOPE_KEY: {PAUSED_FIELD: {"checkpointId": checkpoint_id}}}


def error_meta(*, code: str, limit: str | None = None) -> dict[str, Any]:
    """
    A refusal a worker owns, as distinct from a worker that broke.

    ``budget_exhausted`` naming the limit it hit is the case this exists
    for: nothing another attempt could spend differently, so the caller
    knows not to retry the same way.

    Parameters
    ----------
    code : str
        What kind of refusal this is.
    limit : str | None
        Which limit, when ``code`` names one.

    Returns
    -------
    dict[str, Any]
        A ``metadata`` fragment carrying the refusal.
    """
    error: dict[str, Any] = {"code": code}
    if limit:
        error["limit"] = limit
    return {ENVELOPE_KEY: {ERROR_FIELD: error}}


def read_usage_meta(metadata: Any) -> Usage | None:
    """
    Read what a worker's answer said it spent.

    Parameters
    ----------
    metadata : Any
        The worker's answer's ``metadata`` or ``_meta``.

    Returns
    -------
    Usage | None
        What was reported, or ``None`` when the worker said nothing.
    """
    envelope = _ours(metadata)
    if envelope is None:
        return None
    raw = envelope.get(USAGE_FIELD)
    if not isinstance(raw, Mapping):
        return None
    return Usage(
        input_tokens=raw.get("inputTokens"),
        output_tokens=raw.get("outputTokens"),
        cost=raw.get("cost"),
        currency=str(raw.get("currency") or "USD"),
    )


def steer_notification(session_id: str, instructions: str) -> dict[str, Any]:
    """
    A ``_datalayer/steer`` JSON-RPC notification, ready to send.

    A2A cannot add instructions to a task already running; this is the one
    method the extension adds to do it, over the same connection a worker
    is already being watched on.

    Parameters
    ----------
    session_id : str
        The task or session being steered.
    instructions : str
        What to add to the run before its next model request.

    Returns
    -------
    dict[str, Any]
        A JSON-RPC 2.0 notification (no ``id``: nothing answers it).
    """
    return {
        "jsonrpc": "2.0",
        "method": STEER_METHOD,
        "params": {"sessionId": session_id, "instructions": instructions},
    }
