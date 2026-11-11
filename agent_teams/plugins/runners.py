# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""The ``a2a`` member runner (MH-F07): a seat that is a remote A2A worker.

The run function sends each task to the member's endpoint with fasta2a's
client and answers with the text of the task it became — its artifacts, else
its last agent message — following a task the worker answered before it
ended (``submitted``, ``working``) with ``tasks/get`` until it does. A remote
worker's session is its own: the runner keeps nothing of it between tasks,
and a swap of such a seat is refused by the session verbs (its session is
not the run's).

**An agent on AWS AgentCore** (``agentcore_deployment``) is such a worker,
reached through Datalayer's relay: ``{DATALAYER_AI_AGENTS_URL}/api/ai-agents/v1/
apps/deployments/<uid>/agentcore/a2a/sessions/<session>`` with
``DATALAYER_API_KEY`` as the bearer. The relay exchanges it at Datalayer IAM
for a token of the deployment's agent runtime and posts the JSON-RPC call to
AgentCore on the session named in the path: the seat's session uid, so every
task of the seat, and every ``tasks/get`` of it, reaches the same microVM.
The message's A2A context is that session.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any
from urllib.parse import quote

from ..types import AgentMemberConfig
from .points import MemberRunner, RunFn

#: Where Datalayer's ai-agents service answers, the relay of an AgentCore seat.
AI_AGENTS_URL_ENV = "DATALAYER_AI_AGENTS_URL"

#: The Datalayer key an AgentCore seat is asked with, exchanged by the relay.
API_KEY_ENV = "DATALAYER_API_KEY"

#: The states a task does not leave.
TERMINAL_STATES = frozenset({"completed", "failed", "canceled", "rejected", "input-required"})

#: How long a task is followed with ``tasks/get``, in seconds, and how often.
FOLLOW_SECONDS = 900.0
FOLLOW_EVERY = 1.0


def agentcore_seat_url(deployment_uid: str, session_uid: str, base_url: str) -> str:
    """The relay of an agent on AWS AgentCore, for one session of it."""
    return (
        f"{base_url.rstrip('/')}/api/ai-agents/v1/apps/deployments/{quote(deployment_uid, safe='')}"
        f"/agentcore/a2a/sessions/{quote(session_uid, safe='')}"
    )


def _http_client(headers: dict[str, str]) -> Any:
    """The HTTP client a remote worker is asked with."""
    import httpx

    return httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(300.0, connect=30.0))


def _state_of(task: dict[str, Any]) -> str:
    """A task's state, A2A 1.0's ``TASK_STATE_*`` read as the older names."""
    state = str((task.get("status") or {}).get("state") or "").lower()
    return state.removeprefix("task_state_").replace("_", "-")


def _reached(member: AgentMemberConfig, context: dict[str, Any]) -> tuple[str, dict[str, str], str]:
    """Where the member answers, the headers it is asked with, and the A2A context of its task."""
    if not member.agentcore_deployment:
        return str(member.agent_endpoint or ""), {}, ""
    base = os.environ.get(AI_AGENTS_URL_ENV, "").strip()
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not base or not key:
        raise RuntimeError(
            f"{member.name} runs on AWS AgentCore (deployment {member.agentcore_deployment}): "
            f"it is asked through Datalayer, so {AI_AGENTS_URL_ENV} and {API_KEY_ENV} are both set."
        )
    session = str(((context or {}).get("session") or {}).get("uid") or "")
    if not session:
        raise RuntimeError(
            f"{member.name} runs on AWS AgentCore: its task is a turn of its seat's session, "
            "and the run gave it none."
        )
    return (
        agentcore_seat_url(member.agentcore_deployment, session, base),
        {"Authorization": f"Bearer {key}"},
        session,
    )


def a2a_run_factory(member: AgentMemberConfig) -> RunFn:
    """A run function that sends tasks to the member's A2A endpoint."""

    async def _a2a_run(prompt: str, context: dict[str, Any] | None = None) -> str:
        endpoint, headers, context_id = _reached(member, context or {})
        try:
            from fasta2a.client import A2AClient
            from fasta2a.schema import Message as A2AMessage
            from fasta2a.schema import Part
        except ImportError as error:  # pragma: no cover - the extra names it
            raise RuntimeError(
                f"{member.name} is a remote A2A worker at {endpoint}: `pip install agent-teams[a2a]` to reach it"
            ) from error

        # `agent`, not `base_url` — see the note in a2a/channel.py.
        client = A2AClient(agent=endpoint, http_client=_http_client(headers))
        try:
            told = {k: v for k, v in (context or {}).items() if k != "session"}
            message = A2AMessage(
                role="user",
                message_id=str(uuid.uuid4()),
                parts=[Part(text=prompt)],
                metadata=told,
            )
            if context_id:
                message["context_id"] = context_id
            response = await client.send_message(message)
            result = response.get("result")
            if result is None:
                error = response.get("error", {})
                raise RuntimeError(
                    f"A2A error from {endpoint}: {error.get('message', 'Unknown error')}"
                )
            # A2A v1: the result names what it is — `{"task": …}` or `{"message": …}`.
            if isinstance(result, dict) and isinstance(result.get("task"), dict):
                task = await _followed(client, result["task"], endpoint)
                if _state_of(task) != "completed":
                    raise RuntimeError(
                        f"The task of {member.name} at {endpoint} ended {_state_of(task)}: "
                        f"{extract_text_from_a2a_task(task) or 'it said nothing more'}"
                    )
                return extract_text_from_a2a_task(task)
            if isinstance(result, dict) and isinstance(result.get("message"), dict):
                return extract_text_from_parts(result["message"].get("parts", []))
            return str(result)
        finally:
            await client.http_client.aclose()

    return _a2a_run


async def _followed(client: Any, task: dict[str, Any], endpoint: str) -> dict[str, Any]:
    """The task once it ended, asked with ``tasks/get`` while it has not."""
    deadline = time.monotonic() + FOLLOW_SECONDS
    while _state_of(task) not in TERMINAL_STATES:
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"The task {task.get('id')} at {endpoint} did not end "
                f"within {FOLLOW_SECONDS:.0f} s."
            )
        await asyncio.sleep(FOLLOW_EVERY)
        answer = await client.get_task(str(task.get("id") or ""))
        got = answer.get("result") if isinstance(answer, dict) else None
        if not isinstance(got, dict):
            error = (answer or {}).get("error") or {}
            raise RuntimeError(
                f"A2A error from {endpoint} on task {task.get('id')}: "
                f"{error.get('message', 'no task')}"
            )
        task = got.get("task") if isinstance(got.get("task"), dict) else got
    return task


def a2a_runner() -> MemberRunner:
    """The ``a2a`` runner agent-teams contributes."""
    return MemberRunner("a2a", a2a_run_factory)


def extract_text_from_a2a_task(task: dict[str, Any]) -> str:
    """Extract text output from an A2A Task response: its artifacts, else its last agent message."""
    artifacts = task.get("artifacts", [])
    if artifacts:
        texts: list[str] = []
        for art in artifacts:
            for part in art.get("parts", []):
                if "text" in part:
                    texts.append(part.get("text", ""))
        if texts:
            return "\n".join(texts)
    history = task.get("history", [])
    for msg in reversed(history):
        if msg.get("role") == "agent":
            return extract_text_from_parts(msg.get("parts", []))
    return ""


def extract_text_from_parts(parts: list[dict[str, Any]]) -> str:
    """Extract text from A2A message parts."""
    return "\n".join(part.get("text", "") for part in parts if "text" in part)


__all__ = [
    "a2a_run_factory",
    "a2a_runner",
    "agentcore_seat_url",
    "extract_text_from_a2a_task",
    "extract_text_from_parts",
]
