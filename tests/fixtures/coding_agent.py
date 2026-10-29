# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""A small coding agent of the Agent Client Protocol, for the harness member's tests.

On a prompt it writes ``NOTES.md`` in its session's folder through the
client's ``fs/write_text_file`` (what the Shield decides) and answers
``noted: <the prompt>``; when the prompt says ``push`` it asks permission
for a ``git push`` and says what it was answered.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from acp import (
    PROTOCOL_VERSION,
    Agent,
    Client,
    InitializeResponse,
    NewSessionResponse,
    PromptResponse,
    RequestError,
    run_agent,
    update_agent_message_text,
)
from acp.schema import AgentCapabilities, Implementation, PermissionOption, ToolCallUpdate


class CodingAgent(Agent):
    def __init__(self) -> None:
        self._client: Client | None = None
        self._sessions: dict[str, str] = {}

    def on_connect(self, conn: Client) -> None:
        self._client = conn

    async def initialize(
        self, protocol_version: int, client_capabilities=None, client_info=None, **kwargs: Any
    ):
        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(),
            agent_info=Implementation(name="coding-agent", title="A coding agent", version="0.0.1"),
        )

    async def new_session(self, cwd: str, mcp_servers: list, **kwargs: Any) -> NewSessionResponse:
        session_id = f"s{len(self._sessions) + 1}"
        self._sessions[session_id] = cwd
        return NewSessionResponse(session_id=session_id)

    async def authenticate(self, method_id: str, **kwargs: Any):
        return None

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        return None

    async def prompt(self, prompt: list, session_id: str, **kwargs: Any) -> PromptResponse:
        if session_id not in self._sessions:
            raise RequestError.resource_not_found(session_id)
        assert self._client is not None
        text = " ".join(block.text for block in prompt if getattr(block, "type", None) == "text")
        await self._client.write_text_file(
            session_id=session_id,
            path=os.path.join(self._sessions[session_id], "NOTES.md"),
            content=f"{text}\n",
        )
        if "push" in text:
            answer = await self._client.request_permission(
                session_id=session_id,
                tool_call=ToolCallUpdate(
                    tool_call_id="p1",
                    title="git push origin main",
                    kind="execute",
                    status="pending",
                    raw_input={"command": "git push origin main"},
                ),
                options=[
                    PermissionOption(option_id="allow", name="Allow", kind="allow_once"),
                    PermissionOption(option_id="reject", name="Reject", kind="reject_once"),
                ],
            )
            chosen = getattr(answer.outcome, "option_id", None) or answer.outcome.outcome
            await self._client.session_update(
                session_id=session_id, update=update_agent_message_text(f"push: {chosen}. ")
            )
        await self._client.session_update(
            session_id=session_id, update=update_agent_message_text(f"noted: {text}")
        )
        return PromptResponse(stop_reason="end_turn")


if __name__ == "__main__":
    asyncio.run(run_agent(CodingAgent()))
