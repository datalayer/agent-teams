# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Standard-client compatibility (ORCHESTRATOR.md, O3-05).

Two directions, against ``a2a-sdk`` — the A2A project's own reference
implementation (a2a-protocol.org), pinned in this package's ``test`` extra
and independent of ``fasta2a``, which Datalayer contributes to:

- **A standard client drives a Datalayer worker.** ``a2a-sdk``'s own client
  code — not anything in this package — resolves the card and drives
  :func:`agent_teams.a2a.reference_worker.create_reference_app`.

- **Datalayer drives a standard worker.** The same hand-built JSON-RPC
  dispatch pattern ``tests/test_reference_worker_conformance.py`` uses
  drives a worker built entirely on ``a2a-sdk``'s own server framework
  (:class:`a2a.server.agent_execution.AgentExecutor`), which has never
  heard of the Datalayer orchestration extension. Section 19.8's reduced
  guarantees are the claim under test here: the delegation still
  completes, its extension fields simply are not in the reply, because
  nothing on the other end read them — degrading cleanly, not failing.

The first direction found two real, upstream gaps between what `fasta2a`
actually serves and what the current A2A specification (and `a2a-sdk`, the
project's own reference implementation) requires — not by reading the spec,
by a genuinely independent client refusing to talk to a live worker.
Neither is `agent_teams`' bug to fix: `create_reference_app` hands
`FastA2A` nothing about card or response shape at all, and `agent_runtimes`'
production A2A workers are built on the same `fasta2a`, so both apply
there too. Recorded as tests that assert *today's* actual, non-conformant
behaviour, so a `fasta2a` fix shows up here as a newly-failing assertion
that needs updating, not a silent gap:

1. **The card is missing `url`.** Already found in O4-01, by validating a
   hand-built card against `a2a-sdk`'s `AgentCard` — reproduced here for
   real, over the wire, against a live worker's card resolver.
2. **`message/send` and `message/stream`'s first event both wrap their
   result.** `fasta2a.schema.SendMessageResult` and `StreamResponse` are
   `{task: Task}` / `{message: Message}` — TypedDicts with named,
   optional fields — where the specification (and `a2a.types.
   SendMessageSuccessResponse.result: Task | Message`) requires the bare
   object directly, no wrapper. Both `a2a-sdk`'s deprecated `A2AClient`
   and its replacement `ClientFactory` share the same JSON-RPC transport
   and fail identically: `pydantic.ValidationError`, on the very first
   response of any exchange. This is not a corner case — no
   specification-conformant A2A client can complete `message/send`
   against any `fasta2a`-based worker today, Datalayer's own production
   ones included.
"""

from __future__ import annotations

import uuid
from typing import Any

import anyio
import httpx
import pydantic
import pytest
from a2a.client import A2ACardResolver, A2AClient
from a2a.client.errors import A2AClientJSONError
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Message,
    MessageSendParams,
    Part,
    Role,
    SendMessageRequest,
    TextPart,
)

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


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class TestAStandardClientDrivesADatalayerWorker:
    """Direction one: `a2a-sdk`'s own client code against this package's own worker."""

    async def test_the_cards_own_resolver_refuses_what_fasta2a_actually_serves(
        self,
    ) -> None:
        """Gap 1 — `fasta2a` writes `supportedInterfaces`, never the required top-level `url`."""
        app = create_reference_app()
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=transport, base_url="http://reference-worker"
            ) as httpx_client:
                resolver = A2ACardResolver(
                    httpx_client, base_url="http://reference-worker"
                )
                with pytest.raises(A2AClientJSONError, match="url"):
                    await resolver.get_agent_card()

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    async def test_send_message_wraps_its_result_where_the_spec_wants_it_bare(
        self,
    ) -> None:
        """Gap 2 — `fasta2a.schema.SendMessageResult` is `{task: Task}`, not `Task` itself.

        Deliberately exercised through `A2AClient`, which `a2a-sdk` 0.3.26
        deprecates in favour of `ClientFactory` — both share the one
        JSON-RPC transport this gap actually lives in, so either
        demonstrates it equally; `A2AClient`'s single, direct
        request/response call is the more legible one to pin this test to.
        """
        app = create_reference_app()
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=transport, base_url="http://reference-worker"
            ) as httpx_client:
                # A card `a2a-sdk` will accept, worked around exactly as
                # gap 1's own test proves is necessary — this test is about
                # the *next* gap, not a repeat of the first one.
                raw = (await httpx_client.get("/.well-known/agent-card.json")).json()
                card = AgentCard.model_validate(
                    {**raw, "url": "http://reference-worker"}
                )
                client = A2AClient(httpx_client, agent_card=card)
                request = SendMessageRequest(
                    id=uuid.uuid4().hex,
                    params=MessageSendParams(
                        message=Message(
                            message_id=uuid.uuid4().hex,
                            role=Role.user,
                            parts=[Part(root=TextPart(text="hello"))],
                        )
                    ),
                )
                with pytest.raises(pydantic.ValidationError, match="Task"):
                    await client.send_message(request)

    async def test_the_request_a_standard_client_builds_is_answered_correctly(
        self,
    ) -> None:
        """What still genuinely interoperates, despite both gaps above.

        `a2a-sdk`'s own `Message`/`Part`/`SendMessageRequest` models build
        the request — proving the request side is fully standard — sent
        over raw HTTP because gap 2 means no A2A client library can parse
        the reply; the reply is unwrapped by hand (`result["task"]`, not
        `result`) to check the worker actually answered it, correctly and
        with the extension's fields, once a client gets past the two gaps
        above.
        """
        app = create_reference_app()
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=transport, base_url="http://reference-worker"
            ) as httpx_client:
                request = SendMessageRequest(
                    id=uuid.uuid4().hex,
                    params=MessageSendParams(
                        message=Message(
                            message_id=uuid.uuid4().hex,
                            role=Role.user,
                            parts=[
                                Part(root=TextPart(text="hello from a standard client"))
                            ],
                        )
                    ),
                )
                response = await httpx_client.post(
                    "/",
                    content=request.model_dump_json(by_alias=True, exclude_none=True),
                    headers={
                        "content-type": "application/json",
                        A2A_EXTENSIONS_HEADER: ORCHESTRATION_EXTENSION_URI,
                    },
                )
                response.raise_for_status()
                task_id = response.json()["result"]["task"]["id"]

                for _ in range(200):
                    poll = await httpx_client.post(
                        "/",
                        json={
                            "jsonrpc": "2.0",
                            "id": uuid.uuid4().hex,
                            "method": "tasks/get",
                            "params": {"id": task_id},
                        },
                    )
                    task = poll.json()["result"]
                    if task["status"]["state"] in {
                        "completed",
                        "failed",
                        "canceled",
                        "rejected",
                    }:
                        break
                    await anyio.sleep(0.005)

                assert task["status"]["state"] == "completed"
                reply = task["history"][-1]
                assert "hello from a standard client" in reply["parts"][0]["text"]
                usage = read_usage_meta(reply.get("metadata"))
                assert usage is not None and usage.output_tokens is not None


class _EchoExecutor(AgentExecutor):
    """A worker with no idea the Datalayer orchestration extension exists.

    Built entirely on `a2a-sdk`'s own server framework — not `fasta2a` —
    so this is a genuinely independent worker, standing in for any A2A
    implementation Datalayer has never heard of either.
    """

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        text = context.get_user_input()
        await updater.complete(
            updater.new_agent_message([Part(root=TextPart(text=f"echo: {text}"))])
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.cancel()


def _standard_worker_app() -> A2AStarletteApplication:
    card = AgentCard(
        name="standard-worker",
        description="A worker built on a2a-sdk alone, with no knowledge of any Datalayer "
        "extension.",
        url="http://standard-worker",
        version="1.0.0",
        capabilities=AgentCapabilities(extensions=[]),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="echo", name="echo", description="echoes the input", tags=["echo"]
            )
        ],
    )
    handler = DefaultRequestHandler(
        agent_executor=_EchoExecutor(), task_store=InMemoryTaskStore()
    )
    return A2AStarletteApplication(agent_card=card, http_handler=handler)


def _message_request(
    text: str, *, task_id: str, metadata: dict[str, Any] | None = None
) -> dict[str, Any]:
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


class TestDatalayerDrivesAStandardWorker:
    """Direction two: this package's own dispatch pattern against a worker with no extension."""

    async def test_a_delegation_completes_and_its_extension_fields_are_simply_absent(
        self,
    ) -> None:
        app = _standard_worker_app().build()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://standard-worker"
        ) as worker:
            meta = build_delegation_meta(
                _ROOT_EXECUTION,
                budget={"outputTokens": 4000},
                checkpoint_id="ckpt-nobody-here-reads",
            )
            response = await worker.post(
                "/",
                json=_message_request(
                    "hello from Datalayer", task_id=uuid.uuid4().hex, metadata=meta
                ),
                headers={A2A_EXTENSIONS_HEADER: ORCHESTRATION_EXTENSION_URI},
            )
            response.raise_for_status()
            body = response.json()
            # A worker built on a2a-sdk's own handler *does* wait for the
            # executor before answering `message/send` — no polling needed
            # here, unlike the fasta2a-based reference worker.
            result = body["result"]
            assert result["status"]["state"] == "completed"
            # The agent's own answer is `status.message`, not a `history`
            # entry — `a2a-sdk`'s `DefaultRequestHandler` does not append
            # it there the way `fasta2a` does. `history` still holds the
            # *incoming* message exactly as it arrived, `datalayer`
            # envelope included: that is storage, not a leak — the claim
            # under test is about what the worker itself answers with.
            reply = result["status"]["message"]
            assert "echo: hello from Datalayer" in reply["parts"][0]["text"]
            # The whole point of this direction: the envelope was sent,
            # and nothing on the other end read it, wrote to it, or choked
            # on it — the worker's own answer simply carries no
            # `datalayer` key, because nothing here has ever heard of it.
            assert ENVELOPE_KEY not in (reply.get("metadata") or {})
