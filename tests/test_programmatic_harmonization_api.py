"""Feature proof for the versioned programmatic harmonization API."""

from __future__ import annotations

import asyncio
import gzip
import json
import shutil
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient, Response
from netrias_client import NetriasClient

import src.app.dependencies as dependencies
import src.app.programmatic_harmonization as programmatic_harmonization
import src.auth.user_context as user_context
import src.programmatic_api.harmonization_payload as harmonization_payload
from backend.app.error_handlers import GENERIC_API_ERROR_DETAIL
from src.domain.columns import column_key_from_string
from src.domain.dataset_workflow_ids import dataset_workflow_id_from_string
from src.domain.harmonization import HarmonizeStatus
from src.integrations.harmonize import HarmonizeResult
from src.persistence.workflow_state_store import load_workflow_state
from src.storage import (
    LocalWorkflowStorage,
    UserContext,
    VersionToken,
    WorkflowFile,
    WorkflowNotFoundError,
)

pytestmark = pytest.mark.asyncio

_API_KEY = "test-programmatic-api-key-32-bytes"
_PROGRAMMATIC_USER = UserContext(user_id="programmatic-api")


class _EphemeralArtifactStorage:
    """Expose artifacts through a path that expires with its read context."""

    def __init__(self, delegate: LocalWorkflowStorage) -> None:
        self._delegate = delegate
        self.materialized_path: Path | None = None
        self.context_exited = False

    def artifact_version(
        self,
        user: UserContext,
        file_id: str,
        kind: WorkflowFile,
    ) -> VersionToken | None:
        return self._delegate.artifact_version(user, file_id, kind)

    @contextmanager
    def materialize_artifact(
        self,
        user: UserContext,
        file_id: str,
        kind: WorkflowFile,
    ) -> Generator[Path]:
        with self._delegate.materialize_artifact(user, file_id, kind) as source:
            with TemporaryDirectory(prefix="data-chord-ephemeral-test-") as temp_dir:
                materialized = Path(temp_dir) / source.name
                shutil.copy2(source, materialized)
                self.materialized_path = materialized
                try:
                    yield materialized
                finally:
                    self.context_exited = True


def _harmonization_payload(
    *,
    name: str = "patients.csv",
    sheet_name: str | None = None,
    use_cache: bool = False,
) -> bytes:
    envelope = {
        "schemaVersion": "1.0",
        "data_commons_key": "test-data-model",
        "external_version_number": "11.0.4",
        "use_cache": use_cache,
        "document": {
            "name": name,
            "sheetName": sheet_name,
            "header": ["diagnosis"],
            "rows": [["Lung"]],
        },
        "column_mappings": [
            {
                "column_name": "diagnosis",
                "cde_key": "primary_diagnosis",
                "cde_id": 42,
                "harmonization": "harmonizable",
                "alternatives": [],
            }
        ],
    }
    return gzip.compress(json.dumps(envelope, separators=(",", ":")).encode("utf-8"))


async def _wait_for_terminal_job(
    app_client: AsyncClient,
    job_id: str,
) -> Response:
    for _ in range(100):
        response = await app_client.get(
            f"/api/v1/jobs/{job_id}",
            headers={"x-api-key": _API_KEY},
        )
        if response.json()["status"] != "QUEUED":
            return response
        await asyncio.sleep(0.01)
    raise AssertionError("harmonization job did not reach a terminal state")


async def test_pinned_netrias_client_completes_local_service_round_trip(
    app_client: AsyncClient,
    mock_netrias_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: the pinned client sends requests through this no-cost local service.
    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)

    class _BorrowedHttpClient:
        def __init__(self, timeout: object) -> None:
            del timeout

        async def __aenter__(self) -> AsyncClient:
            return app_client

        async def __aexit__(
            self,
            exception_type: object,
            exception: object,
            traceback: object,
        ) -> None:
            del exception_type, exception, traceback

    import netrias_client._core as client_core

    monkeypatch.setattr(client_core.httpx, "AsyncClient", _BorrowedHttpClient)
    source_path = tmp_path / "patients.csv"
    source_path.write_text("diagnosis\nLung\n", encoding="utf-8")
    output_path = tmp_path / "harmonized.csv"
    client = NetriasClient(_API_KEY)
    client.configure(
        harmonization_url="http://test/api",
        timeout=5,
    )
    mapping = {
        "column_mappings": [
            {
                "column_name": "diagnosis",
                "cde_key": "primary_diagnosis",
                "cde_id": 42,
                "harmonization": "harmonizable",
                "alternatives": [],
            }
        ]
    }

    # When: the real client submits, polls, and downloads both artifacts.
    result = await client.harmonize_async(
        source_path,
        mapping,
        "test-data-model",
        external_version_number="11.0.4",
        output_path=output_path,
        use_cache=False,
    )

    # Then: the client accepts the service contract without paid provider calls.
    assert result.status == "succeeded"
    assert result.file_path.read_bytes() == b"diagnosis\nLung\n"
    assert result.manifest_path is not None
    assert result.manifest_path.read_bytes()[:4] == b"PAR1"
    assert mock_netrias_client.run.call_args.kwargs["use_cache"] is False


@pytest.mark.parametrize(
    ("name", "sheet_name"),
    [
        ("patients.csv", None),
        ("patients.tsv", None),
        ("patients.xlsx", "Patients"),
    ],
)
async def test_harmonization_api_uses_shared_job_and_serves_client_artifacts(
    app_client: AsyncClient,
    mock_netrias_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    sheet_name: str | None,
) -> None:
    # Given: the exact gzip envelope built by the pinned client.
    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)

    # When: the client submits, polls, and follows both signed artifact URLs.
    submitted = await app_client.post(
        "/api/v1/jobs/harmonize",
        headers={
            "content-type": "application/octet-stream",
            "x-api-key": _API_KEY,
        },
        content=_harmonization_payload(name=name, sheet_name=sheet_name),
    )

    # Then: one shared durable job owns the inline document and mapping plan.
    assert submitted.status_code == 202
    assert set(submitted.json()) == {"job_id"}
    job_id = cast(str, submitted.json()["job_id"])
    assert len(job_id) == 32
    loaded_state = load_workflow_state(
        dependencies.get_workflow_storage(),
        _PROGRAMMATIC_USER,
        job_id,
    )
    assert loaded_state is not None
    assert loaded_state.state.data_model_version.data_model_key == "test-data-model"
    assert loaded_state.state.data_model_version.external_version_number == "11.0.4"
    assert loaded_state.state.selected_sheet == sheet_name
    assert loaded_state.state.mapping_choices is not None
    assert (
        loaded_state.state.mapping_manifest.records[column_key_from_string("col_0000")].cde_key == "primary_diagnosis"
    )
    status_response = await _wait_for_terminal_job(app_client, job_id)
    assert mock_netrias_client.run.call_args.kwargs["use_cache"] is False
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["status"] == "SUCCEEDED"
    assert set(status_payload) == {"status", "final_url", "manifest_url"}

    final_response = await app_client.get(status_payload["final_url"])
    manifest_response = await app_client.get(status_payload["manifest_url"])
    assert final_response.status_code == 200
    assert final_response.headers["content-type"].startswith("text/csv")
    assert final_response.content == b"diagnosis\nLung\n"
    assert manifest_response.status_code == 200
    assert manifest_response.content[:4] == b"PAR1"


async def test_harmonization_api_rejects_invalid_gzip_without_starting_work(
    app_client: AsyncClient,
    mock_netrias_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: valid authentication but a body that is not the client gzip envelope.
    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)

    # When: the caller submits invalid compressed input.
    response = await app_client.post(
        "/api/v1/jobs/harmonize",
        headers={
            "content-type": "application/octet-stream",
            "x-api-key": _API_KEY,
        },
        content=b"patient-secret-that-is-not-gzip",
    )

    # Then: a safe client error is returned before durable or provider work.
    assert response.status_code == 400
    assert response.json() == {"detail": GENERIC_API_ERROR_DETAIL}
    assert mock_netrias_client.run.call_count == 0


async def test_harmonization_api_rejects_corrupt_deflate_without_starting_work(
    app_client: AsyncClient,
    mock_netrias_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a gzip header followed by an invalid deflate block.
    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)
    corrupt_gzip = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\x03\xff\xff\xff\xff\x00\x00\x00\x00\x00\x00\x00\x00"

    # When: the decoder reads the corrupt compressed stream.
    response = await app_client.post(
        "/api/v1/jobs/harmonize",
        headers={
            "content-type": "application/octet-stream",
            "x-api-key": _API_KEY,
        },
        content=corrupt_gzip,
    )

    # Then: corruption remains a safe client error, not an internal failure.
    assert response.status_code == 400
    assert response.json() == {"detail": GENERIC_API_ERROR_DETAIL}
    assert mock_netrias_client.run.call_count == 0


async def test_harmonization_api_rejects_invalid_envelope_without_starting_work(
    app_client: AsyncClient,
    mock_netrias_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: valid gzip JSON that is not the current client schema.
    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)
    body = gzip.compress(json.dumps({"schemaVersion": "2.0", "patient": "secret"}).encode())

    # When: the API validates the decoded envelope.
    response = await app_client.post(
        "/api/v1/jobs/harmonize",
        headers={
            "content-type": "application/octet-stream",
            "x-api-key": _API_KEY,
        },
        content=body,
    )

    # Then: validation is safe and no provider work starts.
    assert response.status_code == 400
    assert response.json() == {"detail": GENERIC_API_ERROR_DETAIL}
    assert "secret" not in response.text
    assert mock_netrias_client.run.call_count == 0


async def test_harmonization_api_bounds_gzip_expansion_before_json_parsing(
    app_client: AsyncClient,
    mock_netrias_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a small compressed body that expands beyond the configured safe limit.
    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)
    monkeypatch.setattr(harmonization_payload, "_MAX_DECOMPRESSED_BYTES", 16)

    # When: bounded decompression reaches the limit.
    response = await app_client.post(
        "/api/v1/jobs/harmonize",
        headers={
            "content-type": "application/octet-stream",
            "x-api-key": _API_KEY,
        },
        content=gzip.compress(b"{" + (b" " * 16)),
    )

    # Then: the API returns 413 before JSON or provider work.
    assert response.status_code == 413
    assert response.json() == {"detail": GENERIC_API_ERROR_DETAIL}
    assert mock_netrias_client.run.call_count == 0


async def test_harmonization_api_authenticates_before_decoding_body(
    app_client: AsyncClient,
    mock_netrias_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an invalid API key and invalid compressed content.
    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)

    # When: the caller submits both faults.
    response = await app_client.post(
        "/api/v1/jobs/harmonize",
        headers={"x-api-key": "wrong-key"},
        content=b"not-gzip",
    )

    # Then: authentication remains the first boundary.
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid API key."}
    assert mock_netrias_client.run.call_count == 0


async def test_harmonization_api_applies_its_compressed_body_limit(
    app_client: AsyncClient,
    mock_netrias_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a small test limit represents the 10 MiB client transport limit.
    from backend.app import main as app_main

    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)
    monkeypatch.setattr(app_main, "_PROGRAMMATIC_HARMONIZATION_MAX_BODY_BYTES", 8)

    # When: an authenticated request exceeds that transport limit.
    response = await app_client.post(
        "/api/v1/jobs/harmonize",
        headers={
            "content-type": "application/octet-stream",
            "x-api-key": _API_KEY,
        },
        content=b"123456789",
    )

    # Then: the body is rejected before decoding or provider work.
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body is too large."}
    assert mock_netrias_client.run.call_count == 0


async def test_harmonization_api_authenticates_before_applying_body_limit(
    app_client: AsyncClient,
    mock_netrias_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: both the API key and the request size are invalid.
    from backend.app import main as app_main

    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)
    monkeypatch.setattr(app_main, "_PROGRAMMATIC_HARMONIZATION_MAX_BODY_BYTES", 8)

    # When: the request reaches the outer authentication boundary.
    response = await app_client.post(
        "/api/v1/jobs/harmonize",
        headers={
            "content-type": "application/octet-stream",
            "x-api-key": "wrong-key",
        },
        content=b"123456789",
    )

    # Then: authentication wins without reading the oversized body.
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid API key."}
    assert mock_netrias_client.run.call_count == 0


async def test_harmonization_api_reports_safe_terminal_failure(
    app_client: AsyncClient,
    mock_netrias_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the shared harmonizer fails with sensitive internal detail.
    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)
    mock_netrias_client.run.side_effect = None
    mock_netrias_client.run.return_value = HarmonizeResult(
        job_id="provider-job",
        status=HarmonizeStatus.FAILED,
        detail="patient-secret at /private/provider/path",
    )

    # When: the programmatic job completes and the client polls it.
    submitted = await app_client.post(
        "/api/v1/jobs/harmonize",
        headers={
            "content-type": "application/octet-stream",
            "x-api-key": _API_KEY,
        },
        content=_harmonization_payload(),
    )
    assert submitted.status_code == 202
    job_id = submitted.json()["job_id"]
    response = await _wait_for_terminal_job(app_client, job_id)

    # Then: the durable failed state contains no artifact URL or provider detail.
    assert response.status_code == 200
    assert response.json() == {
        "status": "FAILED",
        "errorMessage": "Harmonization failed. Please retry.",
    }
    assert "patient-secret" not in response.text
    assert "/private/provider/path" not in response.text


async def test_harmonization_api_projects_active_shared_job_as_queued(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the shared worker remains active after durable acceptance.
    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)
    release_worker = asyncio.Event()
    worker_started = asyncio.Event()
    observed_user_ids: list[str] = []

    async def _hold_worker(**kwargs: object):
        observed_user_ids.append(cast(UserContext, kwargs["user"]).user_id)
        worker_started.set()
        await release_worker.wait()
        raise RuntimeError("patient-secret at /private/provider/path")

    service = dependencies.get_harmonization_job_service()
    monkeypatch.setattr(service, "_workflow_runner", _hold_worker)

    # When: the client submits and polls before the worker completes.
    submitted = await app_client.post(
        "/api/v1/jobs/harmonize",
        headers={
            "content-type": "application/octet-stream",
            "x-api-key": _API_KEY,
        },
        content=_harmonization_payload(),
    )
    await asyncio.wait_for(worker_started.wait(), timeout=1)
    job_id = submitted.json()["job_id"]
    response = await app_client.get(
        f"/api/v1/jobs/{job_id}",
        headers={"x-api-key": _API_KEY},
    )

    # Then: the client receives one pending status and no artifact URL.
    assert response.status_code == 200
    assert response.json() == {"status": "QUEUED"}
    assert observed_user_ids == ["programmatic-api"]
    release_worker.set()
    terminal = await _wait_for_terminal_job(app_client, job_id)
    assert terminal.json() == {
        "status": "FAILED",
        "errorMessage": "Harmonization failed. Please retry.",
    }
    assert "patient-secret" not in terminal.text
    assert "/private/provider/path" not in terminal.text


async def test_harmonization_api_discards_a_job_rejected_for_capacity(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one shared worker holds the only harmonization slot.
    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)
    release_worker = asyncio.Event()

    async def _hold_worker(**_kwargs: object):
        await release_worker.wait()
        raise RuntimeError("released test worker")

    service = dependencies.get_harmonization_job_service()
    monkeypatch.setattr(service, "_workflow_runner", _hold_worker)
    workflow_ids = iter(
        [
            dataset_workflow_id_from_string("a" * 32),
            dataset_workflow_id_from_string("b" * 32),
        ]
    )
    monkeypatch.setattr(
        programmatic_harmonization,
        "new_dataset_workflow_id",
        lambda: next(workflow_ids),
    )
    headers = {
        "content-type": "application/octet-stream",
        "x-api-key": _API_KEY,
    }
    first = await app_client.post(
        "/api/v1/jobs/harmonize",
        headers=headers,
        content=_harmonization_payload(),
    )
    assert first.status_code == 202

    # When: a second valid submission cannot reserve capacity.
    rejected = await app_client.post(
        "/api/v1/jobs/harmonize",
        headers=headers,
        content=_harmonization_payload(),
    )

    # Then: no durable or scratch data remains for the rejected workflow.
    assert rejected.status_code == 429
    rejected_id = dataset_workflow_id_from_string("b" * 32)
    assert dependencies.get_upload_storage().load(rejected_id) is None
    with pytest.raises(WorkflowNotFoundError):
        dependencies.get_workflow_storage().read_json(
            _PROGRAMMATIC_USER,
            rejected_id,
            WorkflowFile.WORKFLOW_STATE,
        )
    release_worker.set()
    await asyncio.sleep(0)


async def test_harmonization_api_rejects_tampered_artifact_signature(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a completed job exposes a signed final artifact URL.
    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)
    submitted = await app_client.post(
        "/api/v1/jobs/harmonize",
        headers={
            "content-type": "application/octet-stream",
            "x-api-key": _API_KEY,
        },
        content=_harmonization_payload(),
    )
    job_id = submitted.json()["job_id"]
    status_response = await _wait_for_terminal_job(app_client, job_id)
    final_url = cast(str, status_response.json()["final_url"])

    # When: the signature, workflow path, or artifact kind is changed.
    tampered_urls = (
        f"{final_url}x",
        final_url.replace(job_id, "f" * 32),
        final_url.replace("/harmonized?", "/manifest?"),
    )

    # Then: each exact-path signature keeps the artifact private.
    for tampered_url in tampered_urls:
        response = await app_client.get(tampered_url)
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid artifact URL."}


async def test_harmonization_api_streams_before_materialized_artifact_expires(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a completed job and a storage backend with short-lived local materialization.
    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)
    submitted = await app_client.post(
        "/api/v1/jobs/harmonize",
        headers={
            "content-type": "application/octet-stream",
            "x-api-key": _API_KEY,
        },
        content=_harmonization_payload(),
    )
    job_id = submitted.json()["job_id"]
    status_response = await _wait_for_terminal_job(app_client, job_id)
    final_url = cast(str, status_response.json()["final_url"])
    local_storage = dependencies.get_workflow_storage()
    assert isinstance(local_storage, LocalWorkflowStorage)
    ephemeral_storage = _EphemeralArtifactStorage(local_storage)

    # When: StreamingResponse consumes the signed artifact.
    with monkeypatch.context() as storage_patch:
        storage_patch.setattr(
            dependencies,
            "get_workflow_storage",
            lambda: ephemeral_storage,
        )
        response = await app_client.get(final_url)

    # Then: all bytes arrive before storage removes the materialized path.
    assert response.status_code == 200
    assert response.content == b"diagnosis\nLung\n"
    assert ephemeral_storage.context_exited
    assert ephemeral_storage.materialized_path is not None
    assert not ephemeral_storage.materialized_path.exists()


async def test_harmonization_api_rejects_expired_artifact_signature(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a completed job exposes a currently valid signed URL.
    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)
    submitted = await app_client.post(
        "/api/v1/jobs/harmonize",
        headers={
            "content-type": "application/octet-stream",
            "x-api-key": _API_KEY,
        },
        content=_harmonization_payload(),
    )
    job_id = submitted.json()["job_id"]
    status_response = await _wait_for_terminal_job(app_client, job_id)
    final_url = cast(str, status_response.json()["final_url"])

    # When: the URL is used after its expiry time.
    monkeypatch.setattr(user_context.time, "time", lambda: 10**12)
    response = await app_client.get(final_url)

    # Then: the artifact remains private.
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid artifact URL."}


async def test_harmonization_api_has_no_unversioned_alias(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the programmatic API is configured.
    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)

    # When: a caller omits the required /api prefix.
    response = await app_client.post(
        "/v1/jobs/harmonize",
        content=_harmonization_payload(),
    )

    # Then: no compatibility route exists.
    assert response.status_code == 404
