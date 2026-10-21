# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""The ``a2a`` member runner (MH-F07): a seat that is a remote A2A worker.

The run function sends each task to the member's endpoint with fasta2a's
client and answers with the text of the task it became — its artifacts, else
its last agent message. A remote worker's session is its own: the runner
keeps nothing of it between tasks, and a swap of such a seat is refused by
the session verbs (its session is not the run's).
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List

from ..types import AgentMemberConfig
from .points import MemberRunner, RunFn


def a2a_run_factory(member: AgentMemberConfig) -> RunFn:
    """A run function that sends tasks to the member's A2A endpoint."""
    endpoint = member.agent_endpoint

    async def _a2a_run(prompt: str, context: Dict[str, Any] | None = None) -> str:
        try:
            from fasta2a.client import A2AClient
            from fasta2a.schema import Message as A2AMessage
            from fasta2a.schema import Part
        except ImportError as error:  # pragma: no cover - the extra names it
            raise RuntimeError(
                f"{member.name} is a remote A2A worker at {endpoint}: `pip install agent-teams[a2a]` to reach it"
            ) from error

        # `agent`, not `base_url` — see the note in a2a/channel.py.
        client = A2AClient(agent=endpoint)
        try:
            told = {k: v for k, v in (context or {}).items() if k != "session"}
            message = A2AMessage(
                role="user",
                message_id=str(uuid.uuid4()),
                parts=[Part(text=prompt)],
                metadata=told,
            )
            response = await client.send_message(message)
            result = response.get("result")
            if result is None:
                error = response.get("error", {})
                raise RuntimeError(f"A2A error from {endpoint}: {error.get('message', 'Unknown error')}")
            # A2A v1: the result names what it is — `{"task": …}` or `{"message": …}`.
            if isinstance(result, dict) and isinstance(result.get("task"), dict):
                return extract_text_from_a2a_task(result["task"])
            if isinstance(result, dict) and isinstance(result.get("message"), dict):
                return extract_text_from_parts(result["message"].get("parts", []))
            return str(result)
        finally:
            await client.http_client.aclose()

    return _a2a_run


def a2a_runner() -> MemberRunner:
    """The ``a2a`` runner agent-teams contributes."""
    return MemberRunner("a2a", a2a_run_factory)


def extract_text_from_a2a_task(task: Dict[str, Any]) -> str:
    """Extract text output from an A2A Task response: its artifacts, else its last agent message."""
    artifacts = task.get("artifacts", [])
    if artifacts:
        texts: List[str] = []
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


def extract_text_from_parts(parts: List[Dict[str, Any]]) -> str:
    """Extract text from A2A message parts."""
    return "\n".join(part.get("text", "") for part in parts if "text" in part)


__all__ = ["a2a_run_factory", "a2a_runner", "extract_text_from_a2a_task", "extract_text_from_parts"]
