"""Feature tests for operational observability."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

import pytest
from httpx import ASGITransport, AsyncClient

import src.app.dependencies as dependencies
from src.auth.user_context import TRUSTED_PROXY_USER_HEADER
from src.observability.events import (
    REQUEST_ID_HEADER,
    JsonLogFormatter,
    WorkflowEvent,
    WorkflowOperation,
    WorkflowOutcome,
    WorkflowStage,
    bind_request_id,
    log_workflow_event,
    performance_span,
    request_id_from_header,
    reset_request_id,
)
from src.storage import UploadStorage, UserContext
from tests.conftest import (
    TEST_CSV_CONTENT_TYPE,
    TEST_TARGET_SCHEMA,
    confirm_mapping_choices,
    create_csv_content,
    create_harmonized_csv,
    create_manifest_for_file,
    upload_content,
)

pytestmark = pytest.mark.asyncio

GENERIC_API_ERROR_DETAIL = "We couldn't process this request. Please try again."


def _record_field(record: logging.LogRecord, field: str) -> object:
    return record.__dict__[field]


async def test_request_id_rejects_unicode_alphanumeric_text() -> None:
    supplied = "éééééééé"

    assert request_id_from_header(supplied) != supplied


@pytest.mark.parametrize("operation", list(WorkflowOperation))
@pytest.mark.parametrize("outcome", list(WorkflowOutcome))
async def test_workflow_event_name_is_derived_from_operation_and_outcome(
    operation: WorkflowOperation,
    outcome: WorkflowOutcome,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    log_workflow_event(
        WorkflowEvent(
            stage=WorkflowStage.STAGE_1,
            operation=operation,
            outcome=outcome,
        ),
        UserContext(user_id="operator-test"),
    )

    assert _record_field(caplog.records[-1], "event_name") == (
        f"workflow.{operation.value}.{outcome.value}"
    )


async def test_performance_span_logs_completed_duration(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a stable request component and a deterministic elapsed time
    caplog.set_level(logging.INFO)
    clock = iter([10.0, 10.125])
    monkeypatch.setattr("src.observability.events.time.perf_counter", lambda: next(clock))

    # When: the component completes
    with performance_span("stage4.rows.manifest_read"):
        pass

    # Then: operators receive one structured completed span with its duration
    matching_records = [
        record
        for record in caplog.records
        if getattr(record, "event_name", None) == "performance.span.completed"
    ]
    assert len(matching_records) == 1
    assert _record_field(matching_records[0], "span_name") == "stage4.rows.manifest_read"
    assert _record_field(matching_records[0], "duration_ms") == 125


async def test_performance_span_keeps_bound_request_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given: one request id is bound while the app measures work
    caplog.set_level(logging.INFO)
    token = bind_request_id("request-123")
    try:
        # When: the component completes and the structured formatter handles its log
        with performance_span("stage4.rows.manifest_read"):
            pass
        payload = json.loads(JsonLogFormatter().format(caplog.records[-1]))
    finally:
        reset_request_id(token)

    # Then: operators can correlate the component duration with its HTTP request
    assert payload["request_id"] == "request-123"


async def test_performance_span_logs_safe_failure_and_reraises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given: a measured component fails with a message that must not enter logs
    caplog.set_level(logging.INFO)

    # When: the component raises
    with pytest.raises(RuntimeError, match="private value"):
        with performance_span("stage5.summary.summary_build"):
            raise RuntimeError("private value")

    # Then: the original error escapes and the span logs only its safe type
    matching_records = [
        record
        for record in caplog.records
        if getattr(record, "event_name", None) == "performance.span.failed"
    ]
    assert len(matching_records) == 1
    record = matching_records[0]
    assert _record_field(record, "span_name") == "stage5.summary.summary_build"
    assert _record_field(record, "error_type") == "RuntimeError"
    assert "private value" not in record.getMessage()


async def test_review_workflow_logs_major_performance_spans(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given: one completed workflow is ready for Stage 4 and Stage 5 reads
    rows = [["col_a"], ["alpha"], ["beta"]]
    file_id = await upload_content(app_client, create_csv_content(rows), "performance-spans.csv")
    analyze_response = await app_client.post(
        "/stage-1/analyze",
        json={
            "file_id": file_id,
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": "11.0.4",
        },
    )
    assert analyze_response.status_code == 200
    await confirm_mapping_choices(app_client, file_id)
    harmonize_response = await app_client.post("/stage-3/harmonize", json={"file_id": file_id})
    assert harmonize_response.status_code == 200
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})
    caplog.clear()
    caplog.set_level(logging.INFO)

    # When: the app builds the Stage 4 screen and final Stage 5 summary
    stage4_response = await app_client.post("/stage-4/rows", json={"file_id": file_id})
    stage5_response = await app_client.post("/stage-5/summary", json={"file_id": file_id})

    # Then: each request succeeds and its major work is visible as request-correlated spans
    assert stage4_response.status_code == 200
    assert stage5_response.status_code == 200
    completed_spans = {
        str(_record_field(record, "span_name"))
        for record in caplog.records
        if getattr(record, "event_name", None) == "performance.span.completed"
    }
    assert {
        "stage4.rows.ready_capture",
        "stage4.rows.upload_artifact",
        "stage4.rows.source_dataset_read",
        "stage4.rows.manifest_read",
        "stage4.rows.review_state",
        "stage4.rows.pv_load",
        "stage4.rows.cde_mapping",
        "stage4.rows.response_build",
        "stage4.rows.ready_check",
        "stage5.summary.ready_capture",
        "stage5.summary.manifest_read",
        "stage5.summary.pv_load",
        "stage5.summary.upload_metadata",
        "stage5.summary.review_state",
        "stage5.summary.summary_build",
        "stage5.summary.ready_check",
    } <= completed_spans


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

    def fail_create_workflow(*_args: object) -> None:
        raise RuntimeError("workflow record unavailable")

    workflow_storage = dependencies.get_workflow_storage()
    monkeypatch.setattr(workflow_storage, "create_workflow", fail_create_workflow)
    content = create_csv_content([["col_a"], ["alpha"]])
    assert not [
        record for record in caplog.records if getattr(record, "event_name", None) == "workflow.upload.failed"
    ]

    # When: the upload endpoint reaches the failed workflow-storage step
    from backend.app.main import create_app

    transport = ASGITransport(app=create_app(), raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={TRUSTED_PROXY_USER_HEADER: "test-user"},
    ) as non_raising_client:
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
