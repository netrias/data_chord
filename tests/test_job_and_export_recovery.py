"""Red/green probes for accepted job durability and the documented export inventory."""

import asyncio
from io import BytesIO
from zipfile import ZipFile

import pytest
from httpx import ASGITransport, AsyncClient

from src.app import dependencies
from src.persistence.harmonization_job_store import load_harmonization_job
from src.persistence.review_override_store import load_review_overrides_record
from src.storage import LocalWorkflowStorage, WorkflowFile, WorkflowNotFoundError
from tests.conftest import confirm_mapping_choices, upload_content
from tests.review_package import ReviewPackage
from tests.test_programmatic_harmonization_api import (
    _API_KEY,
    _PROGRAMMATIC_USER,
    _harmonization_payload,
)

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("fault", [False, True], ids=["control", "response-read-fault"])
async def test_accepted_job_preserves_owner_upload_and_result(app_client, monkeypatch, fault):
    # Given a valid local API workflow whose worker is paused after admission.
    service = dependencies.get_harmonization_job_service()
    storage = dependencies.get_workflow_storage()
    assert isinstance(storage, LocalWorkflowStorage)
    upload = dependencies.get_upload_storage()
    run = service._workflow_runner
    assert run is not None
    release = asyncio.Event()
    started = asyncio.Event()
    scheduled = []
    queued_ids = []
    read_faults = []
    read = storage.read_json
    write = storage.write_json
    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)
    headers = {"content-type": "application/octet-stream", "x-api-key": _API_KEY}
    submit_in_progress = True

    async def hold_worker(**kwargs):
        started.set()
        scheduled.append(asyncio.current_task())
        await release.wait()
        return await run(**kwargs)

    def record_write(user, file_id, kind, data, expected_version=None):
        result = write(user, file_id, kind, data, expected_version)
        if kind == WorkflowFile.STAGE_THREE_JOB and data["status"] == "queued":
            queued_ids.append(str(file_id))
        return result

    def read_during_submit(user, file_id, kind):
        result = read(user, file_id, kind)
        if (
            fault
            and submit_in_progress
            and kind == WorkflowFile.STAGE_THREE_JOB
            and file_id in queued_ids
            and not read_faults
        ):
            assert result is not None and isinstance(result.data, dict)
            assert result.data["status"] == "queued"
            assert upload.load(file_id) is not None
            metadata = storage._read_metadata(file_id)
            assert metadata is not None and metadata.owner_user_id == _PROGRAMMATIC_USER.user_id
            read_faults.append(str(file_id))
            raise OSError("synthetic transient status-read failure")
        return result

    monkeypatch.setattr(service, "_workflow_runner", hold_worker)
    monkeypatch.setattr(storage, "read_json", read_during_submit)
    monkeypatch.setattr(storage, "write_json", record_write)
    transport = ASGITransport(app=app_client._transport.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as api:
        # When the real submission endpoint accepts the job and the caller polls after release.
        response = await api.post("/api/v1/jobs/harmonize", headers=headers, content=_harmonization_payload())
        submit_in_progress = False
        await asyncio.wait_for(started.wait(), 2)
        assert len(queued_ids) == 1
        job_id = queued_ids[0]
        owner = storage._read_metadata(job_id)
        scratch = upload.load(job_id) is not None
        try:
            durable_queued = load_harmonization_job(storage, _PROGRAMMATIC_USER, job_id)
            durable_source = storage.artifact_version(_PROGRAMMATIC_USER, job_id, WorkflowFile.ORIGINAL_UPLOAD)
        except WorkflowNotFoundError:
            durable_queued = durable_source = None
        release.set()
        await asyncio.gather(*scheduled, return_exceptions=True)
        poll = await api.get(f"/api/v1/jobs/{job_id}", headers=headers)
        terminal = poll.json().get("status") if "application/json" in poll.headers.get("content-type", "") else None

        # Then the legitimate owner can still poll and download the accepted job's output.
        observed = {
            "submit_http": response.status_code,
            "durably_queued_at_admission": bool(queued_ids),
            "worker_started": started.is_set(),
            "injected_fault_count": len(read_faults),
            "owner_after_submit": owner.owner_user_id if owner else None,
            "durable_queued_after_submit": durable_queued.job.status.value if durable_queued else None,
            "durable_source_after_submit": durable_source is not None,
            "scratch_after_submit": scratch,
            "poll_http": poll.status_code,
            "poll_terminal": terminal,
        }

        assert observed["owner_after_submit"] == _PROGRAMMATIC_USER.user_id, observed
        assert observed["durable_queued_after_submit"] == "queued", observed
        assert observed["durable_source_after_submit"] and scratch, observed
        assert response.status_code == 202 and response.json()["job_id"] == job_id, observed
        assert terminal == "SUCCEEDED", observed
        artifact = await api.get(poll.json()["final_url"])
        assert artifact.status_code == 200 and artifact.content == b"diagnosis\nLung\n"


@pytest.mark.parametrize(
    "missing_history,missing_mapping", [(False, False), (True, False), (False, True), (True, True)]
)
async def test_partial_audit_download(app_client, tmp_path, missing_history, missing_mapping):
    # Given a successful browser workflow with all three audit/export artifacts and no overrides.
    file_id = await upload_content(app_client, b"diagnosis\nLung Cancer\n", "verification.csv")
    analyzed = await app_client.post(
        "/stage-1/analyze",
        json={
            "file_id": file_id,
            "data_model_key": "test-data-model",
            "external_version_number": "11.0.4",
        },
    )
    assert analyzed.status_code == 200
    await confirm_mapping_choices(app_client, file_id)
    started = await app_client.post("/stage-3/harmonize", json={"file_id": file_id})
    assert started.status_code == 200
    storage = dependencies.get_workflow_storage()
    assert isinstance(storage, LocalWorkflowStorage)
    user = dependencies.get_user_context()
    for _attempt in range(100):
        job = load_harmonization_job(storage, user, file_id)
        assert job is not None
        if job.job.status.value == "succeeded":
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("The harmonization job did not finish.")
    review = load_review_overrides_record(storage, user, file_id)
    assert review is None or not review.value.events
    assert storage.read_json(user, file_id, WorkflowFile.CDE_MAPPING) is not None
    assert storage.artifact_version(user, file_id, WorkflowFile.HARMONIZATION_MANIFEST_BASE) is not None
    if missing_history:
        with storage.materialize_artifact(user, file_id, WorkflowFile.HARMONIZATION_MANIFEST_BASE) as stored:
            stored.rename(tmp_path / "held-manifest.parquet")
    if missing_mapping:
        storage._json_path(file_id, WorkflowFile.CDE_MAPPING).rename(tmp_path / "held-cde-mapping.json")

    # When the reviewer opens the summary and downloads its available files.
    review = ReviewPackage.read(await app_client.post("/stage-5/review", json={"file_id": file_id}))
    body = review.summary

    # Then the ZIP contains the reviewed data and exactly the recoverable audit files.
    with ZipFile(BytesIO(review.archive)) as archive:
        names = archive.namelist()
        (dataset,) = [name for name in names if name.endswith(".csv")]
        assert archive.read(dataset).decode().splitlines() == ["diagnosis", "Lung Cancer"]
        base = dataset.removesuffix(".csv")
        expected = {dataset}
        if not missing_history:
            expected.add(f"{base}_manifest.json")
        if not missing_mapping:
            expected.add(f"{base}_cde_mapping.json")
        if missing_history or missing_mapping:
            expected.add("AUDIT-WARNING.txt")
            assert set(names) == expected
            warning = archive.read("AUDIT-WARNING.txt").decode().strip()
            assert warning == body["audit_warning"]
            assert ("transformation history" in warning) == missing_history
            assert ("column mapping" in warning) == missing_mapping
            assert "run the workflow again" in warning
        assert set(names) == expected


@pytest.mark.parametrize("committed", [False, True], ids=["before-job-write", "lost-write-receipt"])
async def test_submission_error_cleans_up_only_a_confirmed_unaccepted_job(app_client, monkeypatch, committed):
    # Given a job store that can lose the request or its receipt at the durable acceptance boundary.
    storage = dependencies.get_workflow_storage()
    write = storage.write_json
    affected = []
    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)

    def fail_job_write(user, file_id, kind, data, expected_version=None):
        if kind != WorkflowFile.STAGE_THREE_JOB:
            return write(user, file_id, kind, data, expected_version)
        affected.append(file_id)
        if committed:
            write(user, file_id, kind, data, expected_version)
        raise OSError("job storage interrupted")

    monkeypatch.setattr(storage, "write_json", fail_job_write)

    # When a valid programmatic submission encounters the storage interruption.
    with pytest.raises(OSError, match="job storage interrupted"):
        await app_client.post("/api/v1/jobs/harmonize", content=_harmonization_payload(),
                              headers={"content-type": "application/octet-stream", "x-api-key": _API_KEY})

    # Then job and source survive a lost receipt, while rejected submissions release both stores.
    [file_id] = affected
    assert (dependencies.get_upload_storage().load(file_id) is not None) == committed
    assert isinstance(storage, LocalWorkflowStorage)
    assert (storage._read_metadata(file_id) is not None) == committed
    if committed:
        saved_job = load_harmonization_job(storage, _PROGRAMMATIC_USER, file_id)
        assert saved_job is not None and saved_job.job.status.value == "queued"
        assert storage.artifact_version(_PROGRAMMATIC_USER, file_id, WorkflowFile.ORIGINAL_UPLOAD) is not None
