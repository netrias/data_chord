"""Observable workflow events and request correlation.

Axis of change: how app, browser, and operator evidence uses shared event names
and safe log fields.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final
from uuid import uuid4

from src.storage import UserContext

REQUEST_ID_HEADER: Final = "X-Request-ID"
CLIENT_EVENTS_ENDPOINT: Final = "/client-events"
FILE_ID_PATTERN: Final = r"^[a-f0-9]{8,64}$"

_MAX_STRING_LENGTH: Final = 512
_MAX_LIST_ITEMS: Final = 20
_RESERVED_LOG_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)
_current_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_logger = logging.getLogger("src.observability.events")


class WorkflowEventName(StrEnum):
    UPLOAD_STARTED = "workflow.upload.started"
    UPLOAD_COMPLETED = "workflow.upload.completed"
    UPLOAD_FAILED = "workflow.upload.failed"
    ANALYZE_STARTED = "workflow.analyze.started"
    ANALYZE_COMPLETED = "workflow.analyze.completed"
    ANALYZE_FAILED = "workflow.analyze.failed"
    MAPPING_STARTED = "workflow.mapping.started"
    MAPPING_COMPLETED = "workflow.mapping.completed"
    MAPPING_FAILED = "workflow.mapping.failed"


class ClientEventName(StrEnum):
    FETCH_FAILED = "client.fetch.failed"
    API_ERROR = "client.api.error"


class WorkflowStage(StrEnum):
    STAGE_1 = "stage_1"
    STAGE_2 = "stage_2"
    STAGE_3 = "stage_3"
    STAGE_4 = "stage_4"
    STAGE_5 = "stage_5"


class WorkflowOperation(StrEnum):
    UPLOAD = "upload"
    ANALYZE = "analyze"
    MAPPING = "mapping"


class WorkflowOutcome(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class RequestTrace:
    method: str
    path: str
    status_code: int
    duration_ms: int
    request_id: str
    user_id: str | None


@dataclass(frozen=True)
class WorkflowEvent:
    event_name: WorkflowEventName
    stage: WorkflowStage
    operation: WorkflowOperation
    outcome: WorkflowOutcome
    file_id: str | None = None
    metadata: Mapping[str, object] | None = None


class JsonLogFormatter(logging.Formatter):
    """Render log records as compact JSON while preserving safe ``extra`` fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = current_request_id()
        if request_id is not None:
            payload["request_id"] = request_id
        payload.update(_extra_fields(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_structured_logging() -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    if not root_logger.handlers:
        root_logger.addHandler(logging.StreamHandler())
    formatter = JsonLogFormatter()
    for handler in root_logger.handlers:
        handler.setFormatter(formatter)


def new_request_id() -> str:
    return uuid4().hex[:16]


def request_id_from_header(value: str | None) -> str:
    if value is None:
        return new_request_id()
    candidate = value.strip()
    if _is_safe_request_id(candidate):
        return candidate
    return new_request_id()


def bind_request_id(request_id: str) -> Token[str | None]:
    return _current_request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _current_request_id.reset(token)


def current_request_id() -> str | None:
    return _current_request_id.get()


def elapsed_ms(started_at: float) -> int:
    return max(round((time.perf_counter() - started_at) * 1000), 0)


def log_request(trace: RequestTrace) -> None:
    _logger.info(
        "HTTP request completed",
        extra={
            "event_name": "http.request.completed",
            "request_id": trace.request_id,
            "method": trace.method,
            "path": trace.path,
            "status_code": trace.status_code,
            "duration_ms": trace.duration_ms,
            "user_id": trace.user_id,
        },
    )


def log_workflow_event(event: WorkflowEvent, user: UserContext) -> None:
    extra = {
        "event_name": event.event_name.value,
        "stage": event.stage.value,
        "operation": event.operation.value,
        "outcome": event.outcome.value,
        "file_id": event.file_id,
        "user_id": user.user_id,
        **safe_log_metadata(event.metadata or {}),
    }
    _logger.info("Workflow event", extra=extra)


def log_client_event(event_name: ClientEventName, payload: Mapping[str, object], user: UserContext) -> None:
    _logger.info(
        "Client event reported",
        extra={
            "event_name": event_name.value,
            "user_id": user.user_id,
            **safe_log_metadata(payload),
        },
    )


def safe_log_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    return {key: _safe_log_value(value) for key, value in metadata.items() if _is_safe_key(key)}


def _safe_log_value(value: object) -> object:
    if isinstance(value, str):
        return value[:_MAX_STRING_LENGTH]
    if isinstance(value, int | float | bool) or value is None:
        return value
    if isinstance(value, list | tuple):
        return [_safe_log_value(item) for item in value[:_MAX_LIST_ITEMS]]
    return str(value)[:_MAX_STRING_LENGTH]


def _extra_fields(record: logging.LogRecord) -> dict[str, object]:
    return {
        key: _safe_log_value(value)
        for key, value in record.__dict__.items()
        if key not in _RESERVED_LOG_FIELDS and _is_safe_key(key)
    }


def _is_safe_key(key: object) -> bool:
    return isinstance(key, str) and key.isidentifier() and not key.startswith("_")


def _is_safe_request_id(value: str) -> bool:
    if not 8 <= len(value) <= 64:
        return False
    return all(char.isalnum() or char in {"-", "_"} for char in value)


__all__ = [
    "CLIENT_EVENTS_ENDPOINT",
    "FILE_ID_PATTERN",
    "REQUEST_ID_HEADER",
    "ClientEventName",
    "JsonLogFormatter",
    "RequestTrace",
    "WorkflowEvent",
    "WorkflowEventName",
    "WorkflowOperation",
    "WorkflowOutcome",
    "WorkflowStage",
    "bind_request_id",
    "configure_structured_logging",
    "current_request_id",
    "elapsed_ms",
    "log_client_event",
    "log_request",
    "log_workflow_event",
    "request_id_from_header",
    "reset_request_id",
]
