# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""A reference A2A worker for the Datalayer orchestration extension (ORCHESTRATOR.md, O3-04).

The smallest thing that speaks the extension correctly: no model, no
framework, no Datalayer control plane — a worker built on `fasta2a` alone
and this package's :mod:`agent_teams.a2a.orchestration_extension`, for a
third party (or this repository's own conformance suite,
``tests/test_reference_worker_conformance.py``) to run standalone and drive
with a real A2A client over real HTTP.

What it does, over a plain "echo the objective back" worker:

- Advertises the extension on its agent card (:func:`create_reference_app`).
- Answers a turn with what it "spent" (:func:`agent_teams.a2a.orchestration_extension.usage_meta`)
  — deterministic and free (a character count, not a model call), so a
  conformance run needs no API key.
- Declines a delegation whose budget already reads ``outputTokens: 0``
  with :func:`error_meta`, naming the limit — the one budget scenario a
  worker with no model can still demonstrate honestly.
- Answers a ``pause`` request by ending the turn at a checkpoint it makes
  up on the spot, and echoes a ``checkpoint`` a delegation resumed from
  back in its answer, so a client can assert the resume actually happened.

Run it directly for manual conformance testing::

    python -m agent_teams.a2a.reference_worker  # serves on :8000

Run it in-process, with no socket, the way the conformance suite does::

    from agent_teams.a2a.reference_worker import create_reference_app

    app = create_reference_app()
    # app is a Starlette ASGI app — httpx.ASGITransport(app=app) drives it
    # with a real A2A client and no network.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fasta2a.applications import FastA2A
from fasta2a.broker import Broker, InMemoryBroker
from fasta2a.extensions import activated_extensions
from fasta2a.storage import InMemoryStorage, Storage
from fasta2a.worker import Worker

from .orchestration_extension import (
    ORCHESTRATION_EXTENSION_URI,
    agent_extension,
    error_meta,
    paused_meta,
    read_delegation_meta,
    usage_meta,
)

if TYPE_CHECKING:
    from fasta2a.schema import Artifact, Message, TaskIdParams, TaskSendParams

__all__ = ["ReferenceWorker", "create_reference_app"]


def _text_of(message: Message) -> str:
    """The text parts of a message, joined — the reference worker reads nothing else."""
    return " ".join(
        str(part["text"]) for part in message.get("parts", []) if "text" in part
    )


def _agent_message(context_id: str, text: str) -> Message:
    return {
        "role": "agent",
        "parts": [{"text": text}],
        "message_id": uuid.uuid4().hex,
        "context_id": context_id,
    }


class ReferenceWorker(Worker[None]):
    """Answers every task itself: no queue, no model, nothing but this extension's rules."""

    async def run_task(self, params: TaskSendParams) -> None:
        task_id = params["id"]
        context_id = params["context_id"]
        incoming = params["message"]

        # The extension's own negotiation rule: read the envelope only once
        # the client actually activated it for this request — a plain A2A
        # client sending `datalayer` fields with nothing activated gets a
        # plain answer, exactly as it would from a worker that never heard
        # of the extension.
        active = ORCHESTRATION_EXTENSION_URI in activated_extensions(params)
        delegation = read_delegation_meta(incoming.get("metadata")) if active else None

        if delegation is not None and delegation.pause_requested:
            checkpoint_id = f"ckpt-{uuid.uuid4().hex[:8]}"
            reply = _agent_message(context_id, f"paused at {checkpoint_id}")
            reply["metadata"] = paused_meta(checkpoint_id)
            await self.storage.update_task(
                task_id, state="completed", new_messages=[reply]
            )
            await self.publish_status(task_id, context_id, "completed", reply)
            return

        if (
            delegation is not None
            and delegation.budget is not None
            and delegation.budget.get("outputTokens") == 0
        ):
            reply = _agent_message(
                context_id, "declined: output token budget is exhausted"
            )
            reply["metadata"] = error_meta(
                code="budget_exhausted", limit="output_tokens"
            )
            await self.storage.update_task(
                task_id, state="failed", new_messages=[reply]
            )
            await self.publish_status(task_id, context_id, "failed", reply)
            return

        text = _text_of(incoming)
        answer = (
            f"resumed from {delegation.checkpoint_id}: echo: {text}"
            if delegation is not None and delegation.checkpoint_id
            else f"echo: {text}"
        )
        reply = _agent_message(context_id, answer)
        if active:
            reply["metadata"] = usage_meta(
                input_tokens=len(text), output_tokens=len(answer)
            )
        await self.storage.update_task(task_id, state="completed", new_messages=[reply])
        await self.publish_status(task_id, context_id, "completed", reply)

    async def cancel_task(self, params: TaskIdParams) -> None:
        await self.storage.update_task(params["id"], state="canceled")

    async def build_message_history(self, history: list[Message]) -> list[Any]:
        # No model behind this worker to build a history for; the extension
        # keeps no state of its own beyond what `read_delegation_meta` reads
        # straight off each request.
        return list(history)

    async def build_artifacts(self, result: Any) -> list[Artifact]:
        # This worker answers entirely in its message; it commits nothing.
        return []


def create_reference_app(
    *, broker: Broker | None = None, storage: Storage[None] | None = None
) -> FastA2A:
    """The reference worker, as a standalone ASGI app.

    Parameters
    ----------
    broker, storage : optional
        Override the in-memory defaults — a conformance suite that wants a
        fresh worker per test still gets one by constructing a new app
        rather than sharing process-wide state.

    Returns
    -------
    FastA2A
        A `Starlette` app: serve it with any ASGI server, or drive it with
        `httpx.ASGITransport` and no socket at all.
    """
    a2a_broker = broker or InMemoryBroker()
    a2a_storage = storage or InMemoryStorage()
    worker = ReferenceWorker(broker=a2a_broker, storage=a2a_storage)

    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        async with app.task_manager, worker.run():
            yield

    return FastA2A(
        storage=a2a_storage,
        broker=a2a_broker,
        name="datalayer-orchestration-reference-worker",
        description=(
            "Reference implementation of the Datalayer orchestration extension "
            "(ORCHESTRATOR.md O3-04): echoes its input, reports what it spent, "
            "declines an exhausted budget, and pauses at a checkpoint on request."
        ),
        extensions=[agent_extension()],
        lifespan=lifespan,
    )


if __name__ == "__main__":
    import os

    import uvicorn

    # Matches `agent_teams.cli`'s own default for the same reason: a
    # reference server meant to be reachable, not a production bind —
    # override with REFERENCE_WORKER_HOST for a tighter one.
    host = os.environ.get("REFERENCE_WORKER_HOST", "0.0.0.0")  # noqa: S104
    port = int(os.environ.get("REFERENCE_WORKER_PORT", "8000"))
    uvicorn.run(create_reference_app(), host=host, port=port)
