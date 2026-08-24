"""Public Stage 2-to-3 acceptance and retry contracts."""

from __future__ import annotations

import asyncio
import shutil
import threading
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient

import src.app.dependencies as dependencies
import src.stage_3_harmonize.router as stage_three_router
from src.domain.harmonization import HarmonizeStatus
from src.integrations.harmonize import HarmonizeResult
from src.persistence.harmonization_job_store import load_harmonization_job, save_harmonization_job
from src.persistence.workflow_artifacts import save_harmonized_artifacts
from src.persistence.workflow_state_store import load_workflow_state
from src.stage_3_harmonize.use_cases import (
    RunAuthority,
    StaleStageThreeWorkerError,
    capture_harmonization_artifact_versions,
)
from src.storage import WorkflowArtifactNotFoundError, WorkflowConflictError, WorkflowFile
from tests.conftest import confirm_mapping_choices, create_csv_content, create_test_manifest_parquet, upload_content

pytestmark = pytest.mark.asyncio


async def test_confirmed_choices_and_file_only_retry_reuse_job(
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

    await confirm_mapping_choices(
        app_client,
        file_id,
        manual_overrides={"col_0000": None},
        column_renames={"col_0000": "Primary Diagnosis"},
    )

    # When: Stage 3 accepts the confirmed plan.
    accepted = await app_client.post(
        "/stage-3/harmonize",
        json={"file_id": file_id},
    )

    # Then: the response exposes durable job identity and both records exist.
    assert accepted.status_code == 200
    accepted_job_id = accepted.json()["job_id"]
    loaded_state = load_workflow_state(
        dependencies.get_workflow_storage(),
        dependencies.get_user_context(),
        file_id,
    )
    loaded_job = load_harmonization_job(
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
    stored_job = dependencies.get_workflow_storage().read_json(
        dependencies.get_user_context(),
        file_id,
        WorkflowFile.STAGE_THREE_JOB,
    )
    assert stored_job is not None
    assert stored_job.data == loaded_job.job.to_store()

    # When: Stage 3 retries/resumes with only the workflow id.
    resumed = await app_client.post("/stage-3/harmonize", json={"file_id": file_id})

    # Then: the same accepted job is returned without replaying model or choice fields.
    assert resumed.status_code == 200
    assert resumed.json()["job_id"] == accepted_job_id
    release_worker.set()
    await asyncio.sleep(0)


async def test_expired_worker_fails_authority_check(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired lease cannot publish or invalidate workflow artifacts."""
    release_worker = asyncio.Event()

    async def _hold_accepted_job(*_args: object, **_kwargs: object) -> None:
        await release_worker.wait()

    monkeypatch.setattr(stage_three_router, "_run_stage_three_job", _hold_accepted_job)
    file_id = await upload_content(
        app_client,
        create_csv_content([["diagnosis"], ["Lung"]]),
        "expired-worker.csv",
    )
    analysis = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": "gc", "external_version_number": "11.0.4"},
    )
    assert analysis.status_code == 200
    await confirm_mapping_choices(app_client, file_id)

    accepted = await app_client.post("/stage-3/harmonize", json={"file_id": file_id})
    assert accepted.status_code == 200
    accepted_job = load_harmonization_job(
        dependencies.get_workflow_storage(),
        dependencies.get_user_context(),
        file_id,
    )
    assert accepted_job is not None
    expired_job = replace(
        accepted_job.job,
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    save_harmonization_job(
        dependencies.get_workflow_storage(),
        dependencies.get_user_context(),
        expired_job,
        expected_version=accepted_job.version,
    )

    with pytest.raises(StaleStageThreeWorkerError):
        RunAuthority(
            dependencies.get_workflow_storage(),
            dependencies.get_user_context(),
            accepted_job.job,
        ).require_current()

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
            data_model_version,
            prepared_manifest,
            column_pv_sets,
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
    await confirm_mapping_choices(app_client, file_id)
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
    assert dependencies.get_upload_storage().load_harmonized_path(file_id) is None
    workflow_storage = dependencies.get_workflow_storage()
    user = dependencies.get_user_context()
    for kind in (WorkflowFile.HARMONIZED_OUTPUT, WorkflowFile.HARMONIZATION_MANIFEST_BASE):
        with pytest.raises(WorkflowArtifactNotFoundError):
            with workflow_storage.materialize_artifact(user, file_id, kind):
                pass
    assert (await app_client.post("/stage-4/rows", json={"file_id": file_id})).status_code == 409


async def test_superseded_worker_preserves_prior_review_artifacts(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A stale worker cannot delete review state before its authority check."""
    provider_started = threading.Event()
    release_provider = threading.Event()
    provider_output_paths: list[Path] = []

    class RerunHarmonizer:
        run_count = 0

        def run(  # type: ignore[no-untyped-def]
            self,
            *,
            file_path,
            data_model_version,
            prepared_manifest,
            column_pv_sets,
            output_path,
            sheet_name,
        ):
            self.run_count += 1
            provider_output_paths.append(output_path)
            if self.run_count == 2:
                provider_started.set()
                if not release_provider.wait(timeout=3):
                    raise TimeoutError("test provider was not released")
            shutil.copy2(file_path, output_path)
            manifest_path = tmp_path / f"preserved-artifacts-{self.run_count}.parquet"
            create_test_manifest_parquet(
                manifest_path,
                [{
                    "job_id": f"preserved-artifacts-{self.run_count}",
                    "column_id": 0,
                    "column_name": "diagnosis",
                    "to_harmonize": "Lung",
                    "top_harmonization": "Lung Cancer",
                    "ontology_id": None,
                    "top_harmonizations": ["Lung Cancer"],
                    "match_fidelity": "strong",
                    "row_indices": [0],
                }],
            )
            return HarmonizeResult(
                job_id=f"preserved-artifacts-{self.run_count}",
                status=HarmonizeStatus.SUCCEEDED,
                detail="ok",
                manifest_path=manifest_path,
                output_path=output_path,
            )

    file_id = await upload_content(
        app_client,
        create_csv_content([["diagnosis"], ["Lung"]]),
        "preserved-artifacts.csv",
    )
    analysis = await app_client.post(
        "/stage-1/analyze",
        json={"file_id": file_id, "data_model_key": "gc", "external_version_number": "11.0.4"},
    )
    assert analysis.status_code == 200
    await confirm_mapping_choices(app_client, file_id)

    harmonizer = RerunHarmonizer()
    monkeypatch.setattr(stage_three_router, "get_harmonize_service", lambda: harmonizer)
    first = await app_client.post("/stage-3/harmonize", json={"file_id": file_id})
    assert first.status_code == 200
    assert first.json()["status"] == "succeeded"

    saved = await app_client.post(
        "/stage-4/overrides",
        headers={"If-None-Match": "*"},
        json={
            "file_id": file_id,
            "overrides": {
                "1": {
                    "col_0000": {"human_value": "Reviewed Lung", "original_value": "Lung"},
                },
            },
            "review_state": {
                "review_mode": "column",
                "sort_mode": "original",
                "scroll_mode": False,
                "show_case_only_changes": False,
                "show_unchanged_values": False,
                "column_mode": {"current_unit": 1, "batch_size": 5},
                "row_mode": {"current_unit": 1, "batch_size": 5},
            },
        },
    )
    assert saved.status_code == 200
    workflow_storage = dependencies.get_workflow_storage()
    user = dependencies.get_user_context()
    prior_mapping = workflow_storage.read_json(user, file_id, WorkflowFile.CDE_MAPPING)
    assert prior_mapping is not None

    second = await app_client.post("/stage-3/harmonize", json={"file_id": file_id})
    assert second.status_code == 200
    assert second.json()["status"] == "queued"
    assert provider_started.is_set()
    assert len(provider_output_paths) == 2
    assert provider_output_paths[0] != provider_output_paths[1]

    newer_mapping = {"newer": True}
    workflow_storage.write_json(
        user,
        file_id,
        WorkflowFile.CDE_MAPPING,
        newer_mapping,
        expected_version=prior_mapping.version,
    )
    current_overrides = workflow_storage.read_json(user, file_id, WorkflowFile.REVIEW_OVERRIDES)
    assert current_overrides is not None
    newer_overrides = deepcopy(current_overrides.data)
    assert isinstance(newer_overrides, dict)
    events = newer_overrides.get("events")
    assert isinstance(events, list)
    assert events and isinstance(events[0], dict)
    events[0]["selected_value"] = "Newer reviewer choice"
    workflow_storage.write_json(
        user,
        file_id,
        WorkflowFile.REVIEW_OVERRIDES,
        newer_overrides,
        expected_version=current_overrides.version,
    )

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

    job = second.json()
    for _ in range(100):
        if job["status"] in {"succeeded", "failed"}:
            break
        await asyncio.sleep(0.01)
        job = (
            await app_client.get(
                f"/stage-3/jobs/{job['job_id']}",
                params={"file_id": file_id},
            )
        ).json()

    assert job["status"] == "failed"
    current_mapping = workflow_storage.read_json(user, file_id, WorkflowFile.CDE_MAPPING)
    current_overrides = workflow_storage.read_json(user, file_id, WorkflowFile.REVIEW_OVERRIDES)
    assert current_mapping is not None
    assert current_mapping.data == newer_mapping
    assert current_overrides is not None
    assert current_overrides.data == newer_overrides


async def test_older_worker_cannot_overwrite_newer_stage_three_artifacts(
    app_client: AsyncClient,
    tmp_path: Path,
) -> None:
    """Captured artifact versions reject a stale Stage 3 publication."""
    file_id = await upload_content(
        app_client,
        create_csv_content([["diagnosis"], ["Lung"]]),
        "artifact-cas.csv",
    )
    workflow_storage = dependencies.get_workflow_storage()
    user = dependencies.get_user_context()
    old_output = tmp_path / "old-output.csv"
    old_manifest = tmp_path / "old-manifest.parquet"
    old_output.write_text("diagnosis\nold\n", encoding="utf-8")
    old_manifest.write_bytes(b"old-manifest")
    save_harmonized_artifacts(workflow_storage, user, file_id, old_output, old_manifest)
    captured = capture_harmonization_artifact_versions(workflow_storage, user, file_id)

    newer_output = tmp_path / "new-output.csv"
    newer_manifest = tmp_path / "new-manifest.parquet"
    newer_output.write_text("diagnosis\nnewer\n", encoding="utf-8")
    newer_manifest.write_bytes(b"newer-manifest")
    workflow_storage.write_artifact(
        user,
        file_id,
        WorkflowFile.HARMONIZED_OUTPUT,
        newer_output,
        expected_version=captured.harmonized_output,
    )
    workflow_storage.write_artifact(
        user,
        file_id,
        WorkflowFile.HARMONIZATION_MANIFEST_BASE,
        newer_manifest,
        expected_version=captured.manifest,
    )

    stale_output = tmp_path / "stale-output.csv"
    stale_manifest = tmp_path / "stale-manifest.parquet"
    stale_output.write_text("diagnosis\nstale\n", encoding="utf-8")
    stale_manifest.write_bytes(b"stale-manifest")
    with pytest.raises(WorkflowConflictError):
        save_harmonized_artifacts(
            workflow_storage,
            user,
            file_id,
            stale_output,
            stale_manifest,
            expected_harmonized_output_version=captured.harmonized_output,
            expected_manifest_version=captured.manifest,
        )

    with workflow_storage.materialize_artifact(user, file_id, WorkflowFile.HARMONIZED_OUTPUT) as path:
        assert path.read_text(encoding="utf-8") == "diagnosis\nnewer\n"
    with workflow_storage.materialize_artifact(
        user,
        file_id,
        WorkflowFile.HARMONIZATION_MANIFEST_BASE,
    ) as path:
        assert path.read_bytes() == b"newer-manifest"
