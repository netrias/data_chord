"""Application-level proof for harmonization job admission."""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient

import src.app.dependencies as dependencies
import src.app.harmonization_job_state as job_state
from src.app.harmonization_job_state import HarmonizationCapacityError
from src.app.harmonization_jobs import HarmonizationJobRequest
from src.app.harmonization_results import HarmonizationWorkflowResult
from src.domain.dataset_workflow_ids import dataset_workflow_id_from_string
from src.domain.harmonization import HarmonizeStatus
from src.persistence.harmonization_job_store import HarmonizationJobState, LoadedHarmonizationJob
from src.storage import VersionToken
from tests.conftest import confirm_mapping_choices, create_csv_content, upload_content

pytestmark = pytest.mark.asyncio


async def _prepare_workflow(client: AsyncClient, name: str) -> str:
    file_id = await upload_content(client, create_csv_content([["diagnosis"], ["Lung"]]), name)
    analyzed = await client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": "gc", "external_version_number": "11.0.4"},
    )
    assert analyzed.status_code == 200
    await confirm_mapping_choices(client, file_id)
    return file_id


async def test_service_preserves_caller_polling_job_id(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a confirmed workflow and an application worker that is held open.
    file_id = await _prepare_workflow(app_client, "caller-polling-id.csv")
    release_worker = asyncio.Event()

    async def hold_worker(**_: object) -> None:
        await release_worker.wait()

    service = dependencies.get_harmonization_job_service()
    monkeypatch.setattr(service, "_workflow_runner", hold_worker)

    # When: the caller submits a stable polling identity.
    accepted = await service.submit(
        user=dependencies.get_user_context(),
        request=HarmonizationJobRequest(
            file_id=dataset_workflow_id_from_string(file_id),
            polling_job_id="caller-polling-id",
        ),
    )

    # Then: durable job state uses the caller's identity exactly.
    assert accepted.job.polling_job_id == "caller-polling-id"
    assert accepted.job.job_id == "caller-polling-id"
    release_worker.set()
    await asyncio.sleep(0)


async def test_service_rejects_second_active_job_without_waiting(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one active worker occupies the configured single process slot.
    first_file_id = await _prepare_workflow(app_client, "first-capacity.csv")
    second_file_id = await _prepare_workflow(app_client, "second-capacity.csv")
    release_worker = asyncio.Event()

    async def hold_worker(**_: object) -> None:
        await release_worker.wait()

    service = dependencies.get_harmonization_job_service()
    monkeypatch.setattr(service, "_workflow_runner", hold_worker)
    first = await service.submit(
        user=dependencies.get_user_context(),
        request=HarmonizationJobRequest(file_id=dataset_workflow_id_from_string(first_file_id)),
    )

    # When: another workflow requests admission while the first is active.
    with pytest.raises(HarmonizationCapacityError):
        await service.submit(
            user=dependencies.get_user_context(),
            request=HarmonizationJobRequest(file_id=dataset_workflow_id_from_string(second_file_id)),
        )

    # Then: the first job remains the only accepted active job.
    assert first.job.file_id == first_file_id
    assert service.get(
        user=dependencies.get_user_context(),
        file_id=dataset_workflow_id_from_string(first_file_id),
        requested_job_id=first.job.polling_job_id,
    ) is not None
    release_worker.set()
    await asyncio.sleep(0)


async def test_cleanup_failure_does_not_consume_the_next_job_slot(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one completed job encounters an operating-system error during scratch cleanup.
    first_file_id = await _prepare_workflow(app_client, "cleanup-error-first.csv")
    second_file_id = await _prepare_workflow(app_client, "cleanup-error-second.csv")

    async def complete_worker(**_: object) -> HarmonizationWorkflowResult:
        return HarmonizationWorkflowResult(
            job_id="provider-job",
            status=HarmonizeStatus.FAILED,
            detail="provider failed",
        )

    def fail_cleanup(*_: object) -> None:
        raise OSError("scratch storage unavailable")

    service = dependencies.get_harmonization_job_service()
    monkeypatch.setattr(service, "_workflow_runner", complete_worker)
    monkeypatch.setattr(service, "_cleanup_worker_output", fail_cleanup)
    first = await service.submit(
        user=dependencies.get_user_context(),
        request=HarmonizationJobRequest(file_id=dataset_workflow_id_from_string(first_file_id)),
    )
    assert first.job.status is HarmonizeStatus.FAILED

    # When: a second workflow requests the single process slot.
    second = await service.submit(
        user=dependencies.get_user_context(),
        request=HarmonizationJobRequest(file_id=dataset_workflow_id_from_string(second_file_id)),
    )

    # Then: cleanup failure from the first job did not block new work.
    assert second.job.file_id == second_file_id
    assert second.job.status is HarmonizeStatus.FAILED


async def test_heartbeat_retries_after_a_transient_storage_error(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a current job whose first heartbeat storage read fails.
    accepted_job = HarmonizationJobState.queued(
        polling_job_id="polling-job",
        file_id="deadbeefdeadbeefdeadbeefdeadbeef",
        plan_version="plan-version",
        worker_id="worker-id",
    )
    loaded_job = LoadedHarmonizationJob(accepted_job, VersionToken("version"))
    stop = asyncio.Event()
    load_calls = 0

    def flaky_load(*_: object) -> LoadedHarmonizationJob:
        nonlocal load_calls
        load_calls += 1
        if load_calls == 1:
            raise OSError("temporary storage failure")
        return loaded_job

    def successful_save(*_: object, **__: object) -> LoadedHarmonizationJob:
        stop.set()
        return loaded_job

    monkeypatch.setattr(job_state, "_HEARTBEAT_SECONDS", 0.001)
    monkeypatch.setattr(job_state, "load_harmonization_job", flaky_load)
    monkeypatch.setattr(job_state, "save_harmonization_job", successful_save)

    # When: the worker keeps its lease alive.
    await asyncio.wait_for(
        job_state.heartbeat_harmonization_job(
            workflow_storage=dependencies.get_workflow_storage(),
            user=dependencies.get_user_context(),
            accepted_job=accepted_job,
            stop=stop,
        ),
        timeout=0.1,
    )

    # Then: it retries and writes the next heartbeat instead of stopping.
    assert load_calls == 2
