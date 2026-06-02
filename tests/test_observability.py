"""Feature tests for operational observability."""

from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient

from src.domain.observability import REQUEST_ID_HEADER
from tests.conftest import TEST_CSV_CONTENT_TYPE, create_csv_content

pytestmark = pytest.mark.asyncio


def _record_field(record: logging.LogRecord, field: str) -> object:
    return record.__dict__[field]


async def test_request_id_header_is_returned(app_client: AsyncClient) -> None:
    # Given: a caller supplies no correlation id
    # When: the app handles a normal request
    response = await app_client.get("/stage-1")

    # Then: the response includes a generated request id operators can search
    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER]


async def test_client_event_endpoint_logs_valid_browser_failure(
    app_client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given: the browser observed a fetch failure before the app saw the stage operation
    caplog.set_level(logging.INFO)

    # When: the validated client-event endpoint receives the report
    response = await app_client.post(
        "/client-events",
        json={
            "event_name": "client.fetch.failed",
            "stage": "stage_1",
            "operation": "analyze",
            "path": "/stage-1/analyze",
            "file_id": "abcdef1234567890",
            "error_name": "TypeError",
            "error_message": "Failed to fetch",
            "online": True,
            "timestamp_ms": 1780427494796,
        },
    )

    # Then: the event is accepted and logged with searchable fields
    assert response.status_code == 204
    matching_records = [
        record for record in caplog.records if getattr(record, "event_name", None) == "client.fetch.failed"
    ]
    assert matching_records
    assert _record_field(matching_records[-1], "file_id") == "abcdef1234567890"
    assert _record_field(matching_records[-1], "operation") == "analyze"


async def test_client_event_endpoint_rejects_full_url_path(app_client: AsyncClient) -> None:
    # Given: a browser event tries to send a full URL instead of a safe path
    payload = {
        "event_name": "client.fetch.failed",
        "stage": "stage_1",
        "operation": "analyze",
        "path": "https://example.test/stage-1/analyze",
    }

    # When: the payload crosses the client-event boundary
    response = await app_client.post("/client-events", json=payload)

    # Then: the boundary rejects it instead of logging arbitrary external data
    assert response.status_code == 422


async def test_stage1_upload_emits_workflow_completion_event(
    app_client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given: a valid CSV upload and no prior workflow completion event
    caplog.set_level(logging.INFO)
    content = create_csv_content([["col_a"], ["alpha"]])
    assert not [
        record for record in caplog.records if getattr(record, "event_name", None) == "workflow.upload.completed"
    ]

    # When: the user uploads the file
    response = await app_client.post(
        "/stage-1/upload",
        files={"file": ("observability.csv", content, TEST_CSV_CONTENT_TYPE)},
    )

    # Then: the upload succeeds and the workflow timeline includes file id and size
    assert response.status_code == 201
    file_id = response.json()["file_id"]
    matching_records = [
        record for record in caplog.records if getattr(record, "event_name", None) == "workflow.upload.completed"
    ]
    assert matching_records
    assert _record_field(matching_records[-1], "file_id") == file_id
    assert _record_field(matching_records[-1], "size_bytes") == len(content)


async def test_stage1_upload_logs_failure_after_file_storage(
    app_client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the uploaded file is accepted, but workflow ownership cannot be recorded
    caplog.set_level(logging.INFO)

    def fail_create_workflow_record(*_args: object) -> None:
        raise RuntimeError("workflow record unavailable")

    monkeypatch.setattr("src.stage_1_upload.router.create_workflow_record", fail_create_workflow_record)
    content = create_csv_content([["col_a"], ["alpha"]])
    assert not [
        record for record in caplog.records if getattr(record, "event_name", None) == "workflow.upload.failed"
    ]

    # When: the upload endpoint reaches the failed workflow-storage step
    with pytest.raises(RuntimeError, match="workflow record unavailable"):
        await app_client.post(
            "/stage-1/upload",
            files={"file": ("observability.csv", content, TEST_CSV_CONTENT_TYPE)},
        )

    # Then: the workflow timeline still includes a searchable upload failure event
    matching_records = [
        record for record in caplog.records if getattr(record, "event_name", None) == "workflow.upload.failed"
    ]
    assert matching_records
    assert _record_field(matching_records[-1], "error_type") == "RuntimeError"
