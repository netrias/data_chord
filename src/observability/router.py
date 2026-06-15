"""Routes for operational client events."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

import src.app.dependencies as dependencies
from src.observability.events import CLIENT_EVENTS_ENDPOINT, log_client_event

from .schemas import ClientEventRequest

observability_router = APIRouter(tags=["Observability"])


@observability_router.post(
    CLIENT_EVENTS_ENDPOINT,
    status_code=status.HTTP_204_NO_CONTENT,
    name="client_events",
)
async def collect_client_event(payload: ClientEventRequest) -> Response:
    log_client_event(
        payload.event_name,
        payload.model_dump(exclude_none=True, mode="json"),
        dependencies.get_user_context(),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
