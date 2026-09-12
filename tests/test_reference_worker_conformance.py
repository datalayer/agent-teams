# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""A public conformance suite for the Datalayer orchestration extension (ORCHESTRATOR.md, O3-04).

Runnable by anyone against their own worker, with no Datalayer service —
and run here against :func:`agent_teams.a2a.reference_worker.create_reference_app`,
this repository's own reference implementation. Every request is built by
hand from the wire shapes ``docs/extension-v1.md`` documents (a raw JSON-RPC
body, the ``A2A-Extensions`` header) rather than through any client library
— including this package's own ``build_delegation_meta`` — so what is
actually asserted is the protocol on the wire, not agreement between two
pieces of code that share an author. The client is ``httpx`` over
``ASGITransport``: a real ASGI request/response cycle through the worker's
own routing, no socket, no control plane, nothing Datalayer-specific on
either side of the exchange but this one extension.

Scenarios, each named for what it proves:

- negotiation: a client that never activates the extension gets a plain
  answer with no ``datalayer`` key at all — a worker advertising the
  extension changes nothing for a caller that does not ask for it.
- usage: an activated client gets back what the turn "spent".
- budget: a delegation whose budget already reads zero is declined, naming
  the limit, not silently run anyway.
- pause and resume: a `pause` request ends the turn at a checkpoint, and a
  later delegation naming that checkpoint gets an answer that says so.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import httpx
import pytest

from agent_teams.a2a.orchestration_extension import (
    ENVELOPE_KEY,
    ORCHESTRATION_EXTENSION_URI,
    ExecutionRef,
    build_delegation_meta,
    read_usage_meta,
)
from agent_teams.a2a.reference_worker import create_reference_app

pytestmark = pytest.mark.anyio

A2A_EXTENSIONS_HEADER = "A2A-Extensions"
_ROOT_EXECUTION = ExecutionRef(
    execution_id="exec_1", root_execution_id="exec_1", depth=0
)
_TERMINAL_STATES = {"completed", "failed", "canceled", "rejected"}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@asynccontextmanager
async def running_worker() -> AsyncIterator[httpx.AsyncClient]:
    """A live reference worker, reachable over real ASGI HTTP with no socket.

    A plain `@pytest.fixture` async generator around this (rather than a
    test calling it directly) hands the surrounding task-group's enter and
    exit to two different points in pytest's own fixture machinery, which
    anyio's structured concurrency refuses at teardown ("cancel scope in a
    different task") — so each test opens and closes this within its own
    single `async with`, unbroken.
    """
    app = create_reference_app()
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://reference-worker"
        ) as client:
            yield client


def _message_request(
    text: str, *, task_id: str, metadata: dict[str, Any] | None = None
) -> dict[str, Any]:
    """A bare JSON-RPC `message/send` request — the wire shape, not a library's model of it."""
    message: dict[str, Any] = {
        "kind": "message",
        "role": "user",
        "messageId": uuid.uuid4().hex,
        "parts": [{"kind": "text", "text": text}],
    }
    if metadata:
        message["metadata"] = metadata
    return {
        "jsonrpc": "2.0",
        "id": task_id,
        "method": "message/send",
        "params": {"message": message},
    }


def _get_task_request(task_id: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "tasks/get",
        "params": {"id": task_id},
    }


async def _send(
    worker: httpx.AsyncClient,
    text: str,
    *,
    activate_extension: bool,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Submit a message, then poll until the reference worker has actually answered.

    `message/send` only *submits* — `TaskManager.send_message` hands the
    broker the operation and returns the task as `storage.submit_task` left
    it, still `submitted`; the reference worker's own background loop
    finishes the turn a moment later, off this request-response cycle
    entirely (the same asynchronous task model any real A2A worker uses).
    A production client either polls `tasks/get`, as this does, or opens
    `message/stream` and reads the terminal event off the stream.
    """
    task_id = uuid.uuid4().hex
    headers = (
        {A2A_EXTENSIONS_HEADER: ORCHESTRATION_EXTENSION_URI}
        if activate_extension
        else {}
    )
    response = await worker.post(
        "/",
        json=_message_request(text, task_id=task_id, metadata=metadata),
        headers=headers,
    )
    response.raise_for_status()
    submitted_id = response.json()["result"]["task"]["id"]

    for _ in range(200):
        poll = await worker.post("/", json=_get_task_request(submitted_id))
        poll.raise_for_status()
        task = poll.json()["result"]
        if task["status"]["state"] in _TERMINAL_STATES:
            return task
        await anyio.sleep(0.005)
    raise TimeoutError(f"reference worker never finished task {submitted_id}")


class TestNegotiation:
    async def test_the_card_advertises_the_extension(self) -> None:
        async with running_worker() as worker:
            response = await worker.get("/.well-known/agent-card.json")
            response.raise_for_status()
            card = response.json()
            uris = [entry["uri"] for entry in card["capabilities"]["extensions"]]
            assert ORCHESTRATION_EXTENSION_URI in uris

    async def test_a_client_that_never_activates_it_gets_a_plain_answer(self) -> None:
        async with running_worker() as worker:
            # Sent as if it were activated, but the header — the only thing
            # that actually turns it on — is not: the worker must not read it.
            task = await _send(
                worker,
                "hello",
                activate_extension=False,
                metadata=build_delegation_meta(_ROOT_EXECUTION),
            )
            message = task["history"][-1]
            assert ENVELOPE_KEY not in (message.get("metadata") or {})


class TestUsage:
    async def test_an_activated_client_learns_what_the_turn_spent(self) -> None:
        async with running_worker() as worker:
            task = await _send(worker, "hello there", activate_extension=True)
            message = task["history"][-1]
            usage = read_usage_meta(message.get("metadata"))
            assert usage is not None
            assert usage.input_tokens is not None and usage.input_tokens > 0
            assert usage.output_tokens is not None and usage.output_tokens > 0


class TestBudget:
    async def test_a_zero_output_token_budget_is_declined_naming_the_limit(
        self,
    ) -> None:
        async with running_worker() as worker:
            meta = build_delegation_meta(_ROOT_EXECUTION, budget={"outputTokens": 0})
            task = await _send(worker, "hello", activate_extension=True, metadata=meta)
            assert task["status"]["state"] == "failed"
            message = task["history"][-1]
            error = message["metadata"][ENVELOPE_KEY]["error"]
            assert error == {"code": "budget_exhausted", "limit": "output_tokens"}


class TestPauseAndResume:
    async def test_a_pause_request_ends_the_turn_at_a_checkpoint(self) -> None:
        async with running_worker() as worker:
            meta = build_delegation_meta(_ROOT_EXECUTION, pause=True)
            task = await _send(worker, "hello", activate_extension=True, metadata=meta)
            message = task["history"][-1]
            paused = message["metadata"][ENVELOPE_KEY]["paused"]
            assert paused["checkpointId"]

    async def test_a_delegation_naming_a_checkpoint_is_told_it_resumed(self) -> None:
        async with running_worker() as worker:
            meta = build_delegation_meta(
                _ROOT_EXECUTION, checkpoint_id="ckpt-from-a-prior-run"
            )
            task = await _send(
                worker, "continue please", activate_extension=True, metadata=meta
            )
            text = task["history"][-1]["parts"][0]["text"]
            assert "ckpt-from-a-prior-run" in text
