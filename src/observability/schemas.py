"""Schemas for browser-reported operational events."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.domain.observability import FILE_ID_PATTERN, ClientEventName


class ClientEventRequest(BaseModel):
    event_name: ClientEventName
    stage: str = Field(..., min_length=1, max_length=32, pattern=r"^[a-z0-9_-]+$")
    operation: str = Field(..., min_length=1, max_length=32, pattern=r"^[a-z0-9_-]+$")
    path: str | None = Field(default=None, min_length=1, max_length=256, pattern=r"^/[^?#]*$")
    file_id: str | None = Field(default=None, pattern=FILE_ID_PATTERN)
    error_name: str | None = Field(default=None, max_length=80)
    error_message: str | None = Field(default=None, max_length=512)
    status_code: int | None = Field(default=None, ge=100, le=599)
    online: bool | None = None
    timestamp_ms: int | None = Field(default=None, ge=0)
