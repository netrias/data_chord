"""Feature tests for operational observability."""

from __future__ import annotations

import logging
from collections.abc import Sequence

import pytest
from httpx import ASGITransport, AsyncClient

from src.observability.events import REQUEST_ID_HEADER
from tests.conftest import TEST_CSV_CONTENT_TYPE, create_csv_content

pytestmark = pytest.mark.asyncio

GENERIC_API_ERROR_DETAIL = "We couldn't process this request. Please try again."


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
            "file_id": "abcdef0123456789abcdef0123456789",
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
    assert _record_field(matching_records[-1], "file_id") == "abcdef0123456789abcdef0123456789"
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
    assert response.json()["detail"] == GENERIC_API_ERROR_DETAIL


async def test_request_validation_failure_logs_diagnostic_fields(
    app_client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given: the browser sends a stale analyze payload that fails request validation
    caplog.set_level(logging.INFO)

    # When: FastAPI rejects the request before the route handler runs
    response = await app_client.post(
        "/stage-1/analyze",
        json={
            "file_id": "abcdef0123456789abcdef0123456789",
            "data_model_key": "gc",
        },
    )

    # Then: users get generic detail, while operators get structured validation detail in logs
    assert response.status_code == 422
    assert response.json()["detail"] == GENERIC_API_ERROR_DETAIL
    assert response.headers[REQUEST_ID_HEADER]
    matching_records = [
        record for record in caplog.records if getattr(record, "event_name", None) == "api.request.failed"
    ]
    assert matching_records
    record = matching_records[-1]
    validation_error_locations = _record_field(record, "validation_error_locations")
    validation_error_types = _record_field(record, "validation_error_types")
    assert isinstance(validation_error_locations, Sequence)
    assert isinstance(validation_error_types, Sequence)
    assert _record_field(record, "path") == "/stage-1/analyze"
    assert _record_field(record, "status_code") == 422
    assert _record_field(record, "error_type") == "RequestValidationError"
    assert _record_field(record, "request_id") == response.headers[REQUEST_ID_HEADER]
    assert "body.external_version_number" in validation_error_locations
    assert "missing" in validation_error_types


async def test_http_exception_failure_returns_generic_detail_and_logs_route_detail(
    app_client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given: analyze receives a valid request shape for an upload that does not exist
    caplog.set_level(logging.INFO)

    # When: the route raises an HTTPException
    response = await app_client.post(
        "/stage-1/analyze",
        json={
            "file_id": "abcdef0123456789abcdef0123456789",
            "data_model_key": "gc",
            "external_version_number": "11.0.4",
        },
    )

    # Then: the response is generic, while logs keep the route detail for investigation
    assert response.status_code == 404
    assert response.json()["detail"] == GENERIC_API_ERROR_DETAIL
    matching_records = [
        record for record in caplog.records if getattr(record, "event_name", None) == "api.request.failed"
    ]
    assert matching_records
    record = matching_records[-1]
    assert _record_field(record, "path") == "/stage-1/analyze"
    assert _record_field(record, "status_code") == 404
    assert _record_field(record, "error_type") == "HTTPException"
    assert _record_field(record, "request_id") == response.headers[REQUEST_ID_HEADER]
    assert _record_field(record, "error_detail") == "Upload not found. Please upload again."


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
    from backend.app.main import create_app

    transport = ASGITransport(app=create_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as non_raising_client:
        response = await non_raising_client.post(
            "/stage-1/upload",
            files={"file": ("observability.csv", content, TEST_CSV_CONTENT_TYPE)},
        )

    # Then: the workflow timeline still includes a searchable upload failure event
    assert response.status_code == 500
    assert response.json()["detail"] == GENERIC_API_ERROR_DETAIL
    assert response.headers[REQUEST_ID_HEADER]
    matching_records = [
        record for record in caplog.records if getattr(record, "event_name", None) == "workflow.upload.failed"
    ]
    assert matching_records
    assert _record_field(matching_records[-1], "error_type") == "RuntimeError"
    api_failure_records = [
        record for record in caplog.records if getattr(record, "event_name", None) == "api.request.failed"
    ]
    assert api_failure_records
    assert _record_field(api_failure_records[-1], "path") == "/stage-1/upload"
    assert _record_field(api_failure_records[-1], "status_code") == 500
    assert _record_field(api_failure_records[-1], "error_type") == "RuntimeError"
    assert _record_field(api_failure_records[-1], "request_id") == response.headers[REQUEST_ID_HEADER]
