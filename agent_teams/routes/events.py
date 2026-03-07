# Copyright (c) 2025-2026 Datalayer, Inc.
#
# BSD 3-Clause License

"""Server-Sent Events endpoint for real-time team event streaming.

The UI subscribes to this endpoint to receive live updates about team
lifecycle, task progress, and member status changes.

Supports two streaming modes:
- **Polling** (default): Periodically checks for new team events.
- **A2A streaming** (when fasta2a is available and team uses A2A protocol):
  Uses fasta2a's broker ``subscribe_to_stream`` for real-time SSE.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..manager import TeamManager
from ..types import EventType, OrchestrationProtocol, TeamEvent

logger = logging.getLogger(__name__)

# Lazy import for fasta2a streaming
_FASTA2A_AVAILABLE = False
try:
    from fasta2a.schema import StreamEvent, stream_event_ta

    _FASTA2A_AVAILABLE = True
except ImportError:
    pass

router = APIRouter(prefix="/teams", tags=["events"])


def _get_manager(request: Request) -> TeamManager:
    """Get the TeamManager from the FastAPI app state."""
    manager = getattr(request.app.state, "team_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=500,
            detail="TeamManager not initialized",
        )
    return manager


async def _event_generator(
    manager: TeamManager,
    team_id: str,
    *,
    since: datetime | None = None,
    event_types: set[EventType] | None = None,
    poll_interval: float = 1.0,
) -> AsyncIterator[str]:
    """Yield SSE-formatted event strings.

    Parameters
    ----------
    manager:
        The team manager to read events from.
    team_id:
        The team to monitor.
    since:
        Only yield events after this timestamp.
    event_types:
        If provided, filter to only these event types.
    poll_interval:
        Seconds between polls for new events.
    """
    last_seen_count = 0

    while True:
        try:
            events = manager.get_events(team_id)
        except KeyError:
            # Team was deleted — close the stream
            yield _format_sse(
                {"type": "team_deleted", "team_id": team_id},
                event="error",
            )
            return

        # Only send new events since last poll
        new_events = events[last_seen_count:]
        last_seen_count = len(events)

        for event in new_events:
            if since and event.timestamp <= since:
                continue
            if event_types and event.type not in event_types:
                continue

            yield _format_sse(
                event.model_dump(mode="json"),
                event=event.type.value,
                id=event.id,
            )

        await asyncio.sleep(poll_interval)


def _format_sse(data: dict, event: str | None = None, id: str | None = None) -> str:
    """Format a dict as an SSE message."""
    lines: list[str] = []
    if event:
        lines.append(f"event: {event}")
    if id:
        lines.append(f"id: {id}")
    lines.append(f"data: {json.dumps(data, default=str)}")
    lines.append("")  # blank line terminates the message
    lines.append("")
    return "\n".join(lines)


async def _a2a_event_generator(
    broker: object,
    task_id: str,
    team_id: str,
) -> AsyncIterator[str]:
    """Yield SSE-formatted events from fasta2a's broker streaming.

    This provides real-time A2A protocol events (status updates,
    artifacts, messages) via SSE, using fasta2a's subscription
    mechanism instead of polling.
    """
    try:
        async for event in broker.subscribe_to_stream(task_id):
            # event is a StreamEvent (Task | Message | TaskStatusUpdateEvent | TaskArtifactUpdateEvent)
            event_dict = event if isinstance(event, dict) else {}
            kind = event_dict.get("kind", "unknown")

            # Map A2A event kinds to SSE event names
            sse_event = f"a2a.{kind}"

            # Add team context
            event_dict["_team_id"] = team_id

            yield _format_sse(event_dict, event=sse_event)

            # Check for final event
            if kind == "status-update" and event_dict.get("final", False):
                yield _format_sse(
                    {"type": "stream_complete", "team_id": team_id, "task_id": task_id},
                    event="a2a.complete",
                )
                return

    except Exception as exc:
        logger.exception("A2A stream error for task %s", task_id)
        yield _format_sse(
            {"type": "stream_error", "error": str(exc), "task_id": task_id},
            event="error",
        )


@router.get("/{team_id}/events")
async def stream_events(
    request: Request,
    team_id: str,
    types: str | None = None,
    mode: str | None = None,
    task_id: str | None = None,
) -> StreamingResponse:
    """Stream team events via Server-Sent Events.

    Query Parameters
    ----------------
    types:
        Comma-separated list of event types to filter (e.g.,
        ``task.assigned,task.completed``). When omitted, all events
        are streamed.
    mode:
        Streaming mode: ``poll`` (default) or ``a2a``. When ``a2a``
        is specified and fasta2a is available, uses the A2A broker's
        streaming infrastructure for real-time events.
    task_id:
        When ``mode=a2a``, subscribe to streaming events for this
        specific A2A task.
    """
    manager = _get_manager(request)

    # Verify team exists
    try:
        team = manager.get_team(team_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Team {team_id} not found")

    # Parse event type filter
    event_types: set[EventType] | None = None
    if types:
        try:
            event_types = {EventType(t.strip()) for t in types.split(",")}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid event type: {exc}")

    # Determine streaming mode
    use_a2a_streaming = (
        mode == "a2a"
        and _FASTA2A_AVAILABLE
        and task_id is not None
        and team.config.orchestration_protocol
        in (OrchestrationProtocol.A2A, OrchestrationProtocol.A2A_EXTENDED)
    )

    if use_a2a_streaming:
        broker = manager.get_a2a_broker(team_id)
        if broker is None:
            raise HTTPException(
                status_code=400,
                detail="A2A streaming not available for this team",
            )

        return StreamingResponse(
            _a2a_event_generator(broker, task_id, team_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return StreamingResponse(
        _event_generator(
            manager,
            team_id,
            event_types=event_types,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable Nginx buffering
        },
    )


@router.get("/{team_id}/events/history", response_model=list[TeamEvent])
async def get_events_history(
    request: Request,
    team_id: str,
    limit: int = 100,
) -> list[TeamEvent]:
    """Get historical events (non-streaming, paginated).

    Useful for initial page load before connecting to SSE.

    Query Parameters
    ----------------
    limit:
        Maximum number of events to return (from the most recent).
    """
    manager = _get_manager(request)
    try:
        events = manager.get_events(team_id, limit=limit)
        return events
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Team {team_id} not found")
