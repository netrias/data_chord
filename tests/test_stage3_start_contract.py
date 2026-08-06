"""Public Stage 2-to-3 acceptance and retry contracts."""

from __future__ import annotations

import asyncio
import shutil
import threading
from pathlib import Path

import pytest
from httpx import AsyncClient

import src.app.dependencies as dependencies
import src.stage_3_harmonize.router as stage_three_router
from src.domain.harmonization import HarmonizeStatus
from src.integrations.netrias_harmonize import HarmonizeResult
from src.persistence.workflow_state_store import load_workflow_state
from src.stage_3_harmonize.job_state import load_stage_three_job_state
from src.storage import WorkflowArtifactNotFoundError, WorkflowFile
from tests.conftest import create_csv_content, create_test_manifest_parquet, upload_content

pytestmark = pytest.mark.asyncio


async def test_one_harmonize_post_saves_choices_and_file_only_retry_reuses_job(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reviewer can queue once, then retry without replaying browser-owned plan data."""
    # Given: analysis has established the durable model and mapping plan.
    file_id = await upload_content(
        app_client,
        create_csv_content([["diagnosis"], ["Lung"]]),
        "single-submit.csv",
    )
    analysis = await app_client.post(
        "/stage-1/analyze",
        json={
            "file_id": file_id,
            "data_model_key": "gc",
            "external_version_number": "11.0.4",
        },
    )
    assert analysis.status_code == 200

    release_worker = asyncio.Event()

    async def _hold_accepted_job(*_args: object, **_kwargs: object) -> None:
        await release_worker.wait()

    monkeypatch.setattr(stage_three_router, "_run_stage_three_job", _hold_accepted_job)

    # When: Stage 2 submits the user's choices to the harmonize operation once.
    accepted = await app_client.post(
        "/stage-3/harmonize",
        json={
            "file_id": file_id,
            "data_model_key": "gc",
            "external_version_number": "11.0.4",
            "manual_overrides": {"col_0000": None},
            "column_renames": {"col_0000": "Primary Diagnosis"},
        },
    )

    # Then: the response exposes durable job identity and both records exist.
    assert accepted.status_code == 200
    accepted_job_id = accepted.json()["job_id"]
    loaded_state = load_workflow_state(
        dependencies.get_workflow_storage(),
        dependencies.get_user_context(),
        file_id,
    )
    loaded_job = load_stage_three_job_state(
        dependencies.get_workflow_storage(),
        dependencies.get_user_context(),
        file_id,
    )
    assert loaded_state is not None
    assert loaded_state.state.mapping_choices is not None
    assert loaded_state.state.mapping_choices.column_overrides.to_strings() == {"col_0000": None}
    assert loaded_state.state.mapping_choices.column_renames.to_strings() == {
        "col_0000": "Primary Diagnosis"
    }
    assert loaded_job is not None
    assert loaded_job.job.polling_job_id == accepted_job_id

    # When: Stage 3 retries/resumes with only the workflow id.
    resumed = await app_client.post("/stage-3/harmonize", json={"file_id": file_id})

    # Then: the same accepted job is returned without replaying model or choice fields.
    assert resumed.status_code == 200
    assert resumed.json()["job_id"] == accepted_job_id
    release_worker.set()
    await asyncio.sleep(0)


async def test_worker_with_superseded_plan_cannot_publish_scratch_results(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A mapping change during provider work fails that run and keeps Stage 4 closed."""
    provider_started = threading.Event()
    release_provider = threading.Event()

    class BlockingHarmonizer:
        def run(  # type: ignore[no-untyped-def]
            self,
            *,
            file_path,
            data_model_key,
            external_version_number,
            prepared_manifest,
            output_path,
            sheet_name,
        ):
            provider_started.set()
            if not release_provider.wait(timeout=3):
                raise TimeoutError("test provider was not released")
            shutil.copy2(file_path, output_path)
            manifest_path = tmp_path / "superseded.manifest.parquet"
            create_test_manifest_parquet(manifest_path, [])
            return HarmonizeResult(
                job_id="superseded-provider-job",
                status=HarmonizeStatus.SUCCEEDED,
                detail="ok",
                manifest_path=manifest_path,
                output_path=output_path,
            )

    file_id = await upload_content(
        app_client,
        create_csv_content([["diagnosis"], ["Lung"]]),
        "superseded-plan.csv",
    )
    analysis = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": "gc", "external_version_number": "11.0.4"},
    )
    assert analysis.status_code == 200
    monkeypatch.setattr(stage_three_router, "get_harmonize_service", lambda: BlockingHarmonizer())

    accepted = await app_client.post("/stage-3/harmonize", json={"file_id": file_id})
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "queued"
    assert provider_started.is_set()

    changed = await app_client.post(
        "/stage-2/choices",
        json={
            "file_id": file_id,
            "manual_overrides": {},
            "column_renames": {"col_0000": "Updated Diagnosis"},
        },
    )
    assert changed.status_code == 200
    release_provider.set()

    job = accepted.json()
    for _ in range(100):
        if job["status"] in {"succeeded", "failed"}:
            break
        await asyncio.sleep(0.01)
        job_response = await app_client.get(
            f"/stage-3/jobs/{job['job_id']}",
            params={"file_id": file_id},
        )
        assert job_response.status_code == 200
        job = job_response.json()

    assert job["status"] == "failed"
    assert dependencies.get_upload_storage().load_harmonized_path(file_id) is not None
    workflow_storage = dependencies.get_workflow_storage()
    user = dependencies.get_user_context()
    for kind in (WorkflowFile.HARMONIZED_OUTPUT, WorkflowFile.HARMONIZATION_MANIFEST_BASE):
        with pytest.raises(WorkflowArtifactNotFoundError):
            with workflow_storage.materialize_artifact(user, file_id, kind):
                pass
    assert (await app_client.post("/stage-4/rows", json={"file_id": file_id})).status_code == 404
