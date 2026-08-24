"""Consolidated error handling tests across all endpoints."""

from __future__ import annotations

import asyncio
import errno
import threading
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.types import Message, Scope

from src.domain.cde import DataModelSummary, DataModelVersionInfo
from src.domain.cde_recommendation import RecommendationUnavailableError
from src.domain.reference_data import ReferenceDataUnavailableError
from src.storage import (
    LocalWorkflowStorage,
    UploadStorage,
    WorkflowCleanup,
    WorkflowConflictError,
)
from tests.conftest import TEST_CSV_CONTENT_TYPE, TEST_TARGET_EXTERNAL_VERSION_NUMBER, TEST_TARGET_SCHEMA, upload_file

pytestmark = pytest.mark.asyncio

INVALID_FILE_ID = "deadbeef12345678deadbeef12345678"
GENERIC_API_ERROR_DETAIL = "We couldn't process this request. Please try again."


class TestMissingFileErrors:
    """All endpoints return 404 for non-existent file_id."""

    async def test_analyze_missing_file(self, app_client: AsyncClient) -> None:
        """Analyze returns 404 for unknown file_id."""

        # Given: A file_id that does not exist in storage

        # When: Analyze is called with the non-existent file_id
        response = await app_client.post(
            "/stage-1/analyze",
            json={
                "file_id": INVALID_FILE_ID,
                "data_model_key": TEST_TARGET_SCHEMA,
                "external_version_number": TEST_TARGET_EXTERNAL_VERSION_NUMBER,
            },
        )

        # Then: 404 response with generic user-facing detail
        assert response.status_code == 404
        assert response.json()["detail"] == GENERIC_API_ERROR_DETAIL

    async def test_harmonize_missing_file(self, app_client: AsyncClient) -> None:
        """Harmonize returns 404 for unknown file_id."""

        # Given: A file_id that does not exist in storage

        # When: Harmonize is called with the non-existent file_id
        response = await app_client.post(
            "/stage-3/harmonize",
            json={"file_id": INVALID_FILE_ID},
        )

        # Then: 404 response
        assert response.status_code == 404

    async def test_rows_missing_file(self, app_client: AsyncClient) -> None:
        """Rows returns recovery guidance for an unknown workflow."""

        # Given: A file_id that does not exist in storage (valid hex format)

        # When: Rows are requested with the non-existent file_id
        response = await app_client.post(
            "/stage-4/rows",
            json={"file_id": INVALID_FILE_ID},
        )

        assert response.status_code == 409
        assert "Stage 2" in response.json()["detail"]


    async def test_summary_missing_file(self, app_client: AsyncClient) -> None:
        """Summary returns recovery guidance for an unknown workflow."""

        # Given: A file_id that does not exist in storage (valid hex format)

        # When: Summary is requested with the non-existent file_id
        response = await app_client.post(
            "/stage-5/summary",
            json={"file_id": INVALID_FILE_ID},
        )

        assert response.status_code == 409
        assert "Stage 2" in response.json()["detail"]


async def test_demo_mode_rejects_replacement_uploads(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the application owns one fixed demo upload.
    monkeypatch.setenv("DATA_CHORD_MODE", "demo")

    # When a caller tries to replace it through the normal upload endpoint.
    response = await app_client.post(
        "/stage-1/upload",
        files={"file": ("replacement.csv", b"value\nother\n", TEST_CSV_CONTENT_TYPE)},
    )

    # Then the server preserves the fixed demo-file contract.
    assert response.status_code == 403
    assert response.json() == {"detail": GENERIC_API_ERROR_DETAIL}


class TestHarmonizationNotReadyErrors:
    """Stage 4 and 5 return recovery guidance before current harmonization."""

    async def test_rows_missing_harmonized(
        self,
        app_client: AsyncClient,
        sample_csv_path: Path,
    ) -> None:
        """Rows directs the user back to the missing workflow step."""

        # Given: An uploaded file without harmonized output
        file_id = await upload_file(app_client, sample_csv_path)

        # When: Rows are requested before harmonization
        response = await app_client.post(
            "/stage-4/rows",
            json={"file_id": file_id},
        )

        assert response.status_code == 409
        assert "Stage 2" in response.json()["detail"]

    async def test_summary_missing_harmonized(
        self,
        app_client: AsyncClient,
        sample_csv_path: Path,
    ) -> None:
        """Summary directs the user back to the missing workflow step."""

        # Given: An uploaded file without harmonized output
        file_id = await upload_file(app_client, sample_csv_path)

        # When: Summary is requested before harmonization
        response = await app_client.post(
            "/stage-5/summary",
            json={"file_id": file_id},
        )

        assert response.status_code == 409
        assert "Stage 2" in response.json()["detail"]


class TestDataModelServiceErrors:
    """Data model endpoint error handling."""

    async def test_list_data_models_exposes_only_external_version_identity(
        self, app_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stage 1 does not leak Data Model Store internal version fields."""
        repository = MagicMock()
        repository.list_models.return_value = (
                    DataModelSummary(
                        data_model_key="gc",
                        label="Genomic Commons",
                        versions=[DataModelVersionInfo(external_version_number="11.0.4")],
                    ),
                )
        monkeypatch.setattr(
            "src.app.dependencies.get_reference_data_repository",
            MagicMock(return_value=repository),
        )

        response = await app_client.get("/stage-1/data-models")

        assert response.status_code == 200
        assert response.json() == [
            {
                "data_model_key": "gc",
                "label": "Genomic Commons",
                "versions": [{"external_version_number": "11.0.4"}],
            }
        ]

    async def test_list_data_models_returns_503_when_api_unavailable(
        self, app_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """503 returned when the reference database is unreachable."""
        # Given the reference database is unreachable.
        repository = MagicMock()
        repository.list_models.side_effect = ReferenceDataUnavailableError("Connection failed")
        monkeypatch.setattr(
            "src.app.dependencies.get_reference_data_repository",
            MagicMock(return_value=repository),
        )

        # When: GET /stage-1/data-models is called
        response = await app_client.get("/stage-1/data-models")

        # Then: 503 response with generic user-facing detail
        assert response.status_code == 503
        assert response.json()["detail"] == GENERIC_API_ERROR_DETAIL

    async def test_analyze_hides_mapping_provider_failure_details(
        self,
        app_client: AsyncClient,
        sample_csv_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A reference-data failure becomes a generic 503 response."""
        file_id = await upload_file(app_client, sample_csv_path)
        repository = MagicMock()
        repository.load_model.side_effect = ReferenceDataUnavailableError("database detail")
        monkeypatch.setattr(
            "src.app.dependencies.get_reference_data_repository",
            MagicMock(return_value=repository),
        )

        response = await app_client.post(
            "/stage-1/analyze",
            json={
                "file_id": file_id,
                "data_model_key": TEST_TARGET_SCHEMA,
                "external_version_number": TEST_TARGET_EXTERNAL_VERSION_NUMBER,
            },
        )

        assert response.status_code == 503
        assert response.json() == {"detail": GENERIC_API_ERROR_DETAIL}

    async def test_analyze_reports_total_recommendation_failure_as_unavailable(
        self,
        app_client: AsyncClient,
        sample_csv_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given the upload and reference model are valid, but all CDE model calls failed.
        file_id = await upload_file(app_client, sample_csv_path)
        recommender = MagicMock()
        recommender.recommend = AsyncMock(
            side_effect=RecommendationUnavailableError("private provider detail")
        )
        monkeypatch.setattr(
            "src.app.dependencies.get_cde_recommender",
            MagicMock(return_value=recommender),
        )

        # When Stage 1 analyzes the upload.
        response = await app_client.post(
            "/stage-1/analyze",
            json={
                "file_id": file_id,
                "data_model_key": TEST_TARGET_SCHEMA,
                "external_version_number": TEST_TARGET_EXTERNAL_VERSION_NUMBER,
            },
        )

        # Then it returns a retryable 503 without provider details.
        assert response.status_code == 503
        assert response.json() == {"detail": GENERIC_API_ERROR_DETAIL}


async def test_workflow_write_conflicts_are_globally_reported_as_retryable(
    app_client: AsyncClient,
    sample_csv_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_id = await upload_file(app_client, sample_csv_path)
    monkeypatch.setattr(
        "src.stage_1_upload.router.save_initial_workflow_state",
        MagicMock(side_effect=WorkflowConflictError(file_id)),
    )

    response = await app_client.post(
        "/stage-1/analyze",
        json={
            "file_id": file_id,
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": TEST_TARGET_EXTERNAL_VERSION_NUMBER,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == GENERIC_API_ERROR_DETAIL


class TestUploadValidationErrors:
    """Upload endpoint validates file type and size."""

    async def test_invalid_xlsx_rejected(self, app_client: AsyncClient) -> None:
        """Upload rejects invalid workbook bytes even with an XLSX extension."""

        # Given: A file with .xlsx extension but invalid workbook bytes

        # When: The file is uploaded
        response = await app_client.post(
            "/stage-1/upload",
            files={
                "file": (
                    "test.xlsx",
                    b"fake xlsx content",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )

        # Then: 415 Unsupported Media Type response with generic user-facing detail
        assert response.status_code == 415
        assert response.json()["detail"] == GENERIC_API_ERROR_DETAIL

    async def test_unsupported_content_type_rejected(self, app_client: AsyncClient) -> None:
        """Upload rejects files with unsupported content types."""

        # Given: A file with JSON content type (not supported)

        # When: The file is uploaded
        response = await app_client.post(
            "/stage-1/upload",
            files={"file": ("test.json", b'{"data": "test"}', "application/json")},
        )

        # Then: 415 Unsupported Media Type response
        assert response.status_code == 415

    async def test_oversized_file_rejected(self, app_client: AsyncClient) -> None:
        """Upload rejects files exceeding size limit."""

        # Given: A file exceeding the 25MB size limit
        oversized_content = b"x" * (26 * 1024 * 1024)

        # When: The oversized file is uploaded
        response = await app_client.post(
            "/stage-1/upload",
            files={"file": ("large.csv", oversized_content, TEST_CSV_CONTENT_TYPE)},
        )

        # Then: 413 Payload Too Large response with generic user-facing detail
        assert response.status_code == 413
        assert response.json()["detail"] == GENERIC_API_ERROR_DETAIL

    async def test_full_portable_storage_rejects_upload_before_writing(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given: the portable storage guard reports that its real filesystem is full.
        workflow_storage = LocalWorkflowStorage(tmp_path / "workflow-storage")
        cleanup = WorkflowCleanup(
            workflow_storage,
            temp_storage,
            capacity_bytes=1024,
            required_free_bytes=100,
        )
        monkeypatch.setattr(workflow_storage, "available_bytes", lambda: 0)
        monkeypatch.setattr(temp_storage, "available_bytes", lambda: 0)
        monkeypatch.setattr(
            "src.stage_1_upload.router.dependencies.get_workflow_cleanup",
            lambda: cleanup,
        )
        assert list((temp_storage._data_dir).iterdir()) == []

        # When: a valid upload starts.
        response = await app_client.post(
            "/stage-1/upload",
            files={"file": ("sample.csv", b"value\n1\n", TEST_CSV_CONTENT_TYPE)},
        )

        # Then: the API rejects it before writing scratch data.
        assert response.status_code == 507
        assert list((temp_storage._data_dir).iterdir()) == []

    async def test_disk_full_during_upload_returns_storage_full(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given the preflight check passed but the scratch write reaches disk capacity.
        monkeypatch.setattr(
            temp_storage,
            "store",
            AsyncMock(side_effect=OSError(errno.ENOSPC, "disk full")),
        )

        # When a valid upload starts.
        response = await app_client.post(
            "/stage-1/upload",
            files={"file": ("sample.csv", b"value\n1\n", TEST_CSV_CONTENT_TYPE)},
        )

        # Then the API reports insufficient storage instead of an internal error.
        assert response.status_code == 507

    async def test_successful_upload_runs_cleanup_failure_in_background(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Given portable cleanup will wait and then fail after a successful upload.
        workflow_storage = LocalWorkflowStorage(temp_storage._base_dir / "workflow_storage")
        cleanup = WorkflowCleanup(workflow_storage, temp_storage, capacity_bytes=1024)
        cleanup_started = threading.Event()
        release_cleanup = threading.Event()

        def _wait_then_fail() -> None:
            cleanup_started.set()
            if not release_cleanup.wait(timeout=2):
                raise AssertionError("test did not release cleanup")
            raise OSError("cleanup failed")

        monkeypatch.setattr(cleanup, "run", _wait_then_fail)
        monkeypatch.setattr(
            "src.stage_1_upload.router.dependencies.get_workflow_cleanup",
            lambda: cleanup,
        )
        request = app_client.build_request(
            "POST",
            "/stage-1/upload",
            files={"file": ("sample.csv", b"value\n1\n", TEST_CSV_CONTENT_TYPE)},
        )
        request_body = await request.aread()
        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": request.url.path,
            "raw_path": request.url.raw_path,
            "query_string": request.url.query,
            "headers": [(name.lower(), value) for name, value in request.headers.raw],
            "client": ("127.0.0.1", 123),
            "server": ("test", 80),
            "root_path": "",
        }
        request_delivered = False
        response_finished = asyncio.Event()
        response_statuses: list[int] = []

        async def _receive() -> Message:
            nonlocal request_delivered
            if not request_delivered:
                request_delivered = True
                return {"type": "http.request", "body": request_body, "more_body": False}
            await asyncio.Future()
            raise AssertionError("unreachable")

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_statuses.append(message["status"])
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                response_finished.set()

        transport = cast(ASGITransport, app_client._transport)  # noqa: SLF001 - fixture application

        # When the application serves the upload through its ASGI boundary.
        application_task = asyncio.ensure_future(
            transport.app(scope, _receive, _send),
        )
        await asyncio.wait_for(response_finished.wait(), timeout=2)
        cleanup_did_start = await asyncio.to_thread(cleanup_started.wait, 2)

        # Then the success response is complete while cleanup is still running.
        assert response_statuses == [201]
        assert cleanup_did_start
        assert not application_task.done()
        release_cleanup.set()
        await asyncio.wait_for(application_task, timeout=2)
