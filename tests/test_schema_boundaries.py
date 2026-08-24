"""Malformed durable state must fail closed at typed domain boundaries."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.app.harmonization_readiness import HarmonizationNotReadyError, require_ready_harmonization_workflow
from src.domain.cde import CDEInfo, CdeType
from src.domain.cde_catalog import CdeCatalog
from src.domain.column_cde_map import ColumnCdeOverrides
from src.domain.column_renames import ColumnRenameSet
from src.domain.columns import column_key_from_string
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.dataset_workflow_ids import dataset_workflow_id_from_string
from src.domain.harmonization import HarmonizationManifestSummary, HarmonizeStatus, MatchFidelity, MatchFidelityCount
from src.domain.manifest import ColumnMappingManifest, InvalidMappingManifestError, RecommendationSource
from src.domain.pv_manifest import PVManifest, PvManifestSchemaError
from src.domain.workflow_state import WorkflowState, WorkflowStateSchemaError
from src.persistence.harmonization_job_store import (
    HarmonizationJobState,
    HarmonizationJobUnreadableError,
    save_harmonization_job,
)
from src.persistence.workflow_state_store import save_initial_workflow_state
from src.storage import LocalWorkflowStorage, UserContext, WorkflowFile, WorkflowMetadata

_FILE_ID = dataset_workflow_id_from_string("abcdef0123456789abcdef0123456789")
_OTHER_FILE_ID = dataset_workflow_id_from_string("0123456789abcdef0123456789abcdef")


def _workflow_state() -> WorkflowState:
    return WorkflowState.from_data_model_version(
        _FILE_ID,
        DataModelVersionReference("gc", "11.0.4"),
        ColumnMappingManifest.empty(),
    )


def test_workflow_metadata_rejects_boolean_schema_version() -> None:
    metadata = WorkflowMetadata.create(UserContext(user_id="user-1"), _FILE_ID).to_store()
    metadata["storage_schema_version"] = True

    assert WorkflowMetadata.from_store(metadata) is None


def test_workflow_metadata_rejects_unsupported_schema_versions() -> None:
    metadata = WorkflowMetadata.create(UserContext(user_id="user-1"), _FILE_ID).to_store()

    for schema_version in (0, 2):
        metadata["storage_schema_version"] = schema_version
        assert WorkflowMetadata.from_store(metadata) is None


def test_workflow_state_rejects_boolean_schema_version() -> None:
    with pytest.raises(WorkflowStateSchemaError, match="invalid schema version"):
        WorkflowState.from_store({"schema_version": True}, _FILE_ID)


def test_workflow_state_rejects_older_and_newer_schema_versions() -> None:
    for schema_version in (1, 2, 4):
        with pytest.raises(WorkflowStateSchemaError, match="not supported"):
            WorkflowState.from_store({"schema_version": schema_version}, _FILE_ID)


@pytest.mark.parametrize("selected_sheet", [1, True, [], {}])
def test_workflow_state_rejects_invalid_selected_sheet(selected_sheet: object) -> None:
    stored = _workflow_state().to_store()
    stored["selected_sheet"] = selected_sheet

    with pytest.raises(WorkflowStateSchemaError, match="invalid selected_sheet"):
        WorkflowState.from_store(stored, _FILE_ID)


def test_workflow_state_requires_selected_sheet_field() -> None:
    stored = _workflow_state().to_store()
    del stored["selected_sheet"]

    with pytest.raises(WorkflowStateSchemaError, match="missing selected_sheet"):
        WorkflowState.from_store(stored, _FILE_ID)


@pytest.mark.parametrize(
    ("manual_overrides", "column_renames"),
    [
        ({}, None),
        (None, {}),
        ([], {}),
        ({}, []),
        ({"col_0000": 1}, {}),
        ({}, {"col_0000": None}),
        (None, None),
    ],
)
def test_workflow_state_rejects_partial_or_malformed_mapping_choices(
    manual_overrides: object,
    column_renames: object,
) -> None:
    stored = _workflow_state().to_store()
    stored["manual_overrides"] = manual_overrides
    stored["column_renames"] = column_renames

    with pytest.raises(WorkflowStateSchemaError, match="mapping choices"):
        WorkflowState.from_store(stored, _FILE_ID)


def test_workflow_state_allows_both_mapping_choice_fields_to_be_absent() -> None:
    loaded = WorkflowState.from_store(_workflow_state().to_store(), _FILE_ID)

    assert loaded.mapping_choices is None


def test_pv_manifest_rejects_boolean_schema_version() -> None:
    with pytest.raises(PvManifestSchemaError):
        PVManifest.from_store({"schema_version": True})


def test_stage_three_job_rejects_boolean_schema_version() -> None:
    with pytest.raises(HarmonizationJobUnreadableError, match="invalid schema version"):
        HarmonizationJobState.from_store({"schema_version": True}, _FILE_ID)


def test_stage_three_job_rejects_older_and_newer_schema_versions() -> None:
    for schema_version in (1, 2, 4):
        with pytest.raises(HarmonizationJobUnreadableError, match="not supported"):
            HarmonizationJobState.from_store({"schema_version": schema_version}, _FILE_ID)


def test_current_harmonization_job_round_trips_without_derived_url() -> None:
    job = replace(
        HarmonizationJobState.queued(
            polling_job_id="job-1",
            file_id=_FILE_ID,
            plan_version="plan-1",
            worker_id="worker-1",
            now=datetime(2026, 8, 6, tzinfo=UTC),
        ),
        manifest_summary=HarmonizationManifestSummary(
            total_terms=3,
            changed_terms=2,
            match_fidelity_counts=[
                MatchFidelityCount(id=MatchFidelity.STRONG, label="Strong", term_count=1),
                MatchFidelityCount(id=MatchFidelity.PARTIAL, label="Partial", term_count=1),
                MatchFidelityCount(id=MatchFidelity.APPROXIMATE, label="Approximate", term_count=1),
            ],
        ),
    )

    stored = job.to_store()
    loaded = HarmonizationJobState.from_store(stored, _FILE_ID)

    assert loaded == job
    assert stored == {
        "schema_version": 3,
        "polling_job_id": "job-1",
        "job_id": "job-1",
        "file_id": _FILE_ID,
        "status": "queued",
        "detail": "Harmonization job accepted.",
        "started_at": "2026-08-06T00:00:00+00:00",
        "plan_version": "plan-1",
        "worker_id": "worker-1",
        "lease_expires_at": "2026-08-06T00:00:45+00:00",
        "job_id_available": False,
        "manifest_summary": {
            "total_terms": 3,
            "changed_terms": 2,
            "match_fidelity_counts": [
                {"id": "strong", "label": "Strong", "term_count": 1},
                {"id": "partial", "label": "Partial", "term_count": 1},
                {"id": "approximate", "label": "Approximate", "term_count": 1},
            ],
            "non_conformant_terms": 0,
            "column_breakdowns": [],
        },
    }


@pytest.mark.parametrize(
    "required_field",
    [
        "schema_version",
        "polling_job_id",
        "job_id",
        "file_id",
        "status",
        "detail",
        "started_at",
        "plan_version",
        "worker_id",
        "lease_expires_at",
        "job_id_available",
    ],
)
def test_current_harmonization_job_requires_every_current_field(required_field: str) -> None:
    stored = HarmonizationJobState.queued(
        polling_job_id="job-1",
        file_id=_FILE_ID,
        plan_version="plan-1",
        worker_id="worker-1",
        now=datetime(2026, 8, 6, tzinfo=UTC),
    ).to_store()
    del stored[required_field]

    with pytest.raises(HarmonizationJobUnreadableError):
        HarmonizationJobState.from_store(stored, _FILE_ID)


def _readiness_context(
    tmp_path: Path,
) -> tuple[LocalWorkflowStorage, UserContext, str]:
    storage = LocalWorkflowStorage(tmp_path / "workflow-storage")
    user = UserContext(user_id="alice")
    storage.create_workflow(user, _FILE_ID)
    loaded = save_initial_workflow_state(storage, user, _workflow_state())
    return storage, user, loaded.version.value


def _job_for_readiness(
    *,
    plan_version: str,
    status: HarmonizeStatus,
    file_id: str = _FILE_ID,
) -> HarmonizationJobState:
    now = datetime(2026, 8, 6, tzinfo=UTC)
    return replace(
        HarmonizationJobState.queued(
            polling_job_id="job-1",
            file_id=file_id,
            plan_version=plan_version,
            worker_id="worker-1",
            now=now,
        ),
        status=status,
        lease_expires_at=now,
    )


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (HarmonizeStatus.QUEUED, "still running"),
        (HarmonizeStatus.FAILED, "failed"),
    ],
)
def test_harmonization_readiness_rejects_non_successful_jobs(
    tmp_path: Path,
    status: HarmonizeStatus,
    message: str,
) -> None:
    storage, user, plan_version = _readiness_context(tmp_path)
    save_harmonization_job(
        storage,
        user,
        _job_for_readiness(plan_version=plan_version, status=status),
        expected_version=None,
    )

    with pytest.raises(HarmonizationNotReadyError, match=message):
        require_ready_harmonization_workflow(storage, user, _FILE_ID)


def test_harmonization_readiness_rejects_missing_job(tmp_path: Path) -> None:
    storage, user, _ = _readiness_context(tmp_path)

    with pytest.raises(HarmonizationNotReadyError, match="has not run"):
        require_ready_harmonization_workflow(storage, user, _FILE_ID)


def test_harmonization_readiness_rejects_missing_workflow(tmp_path: Path) -> None:
    storage = LocalWorkflowStorage(tmp_path / "workflow-storage")
    user = UserContext(user_id="alice")

    with pytest.raises(HarmonizationNotReadyError, match="workflow is not ready"):
        require_ready_harmonization_workflow(storage, user, _FILE_ID)


def test_harmonization_readiness_rejects_corrupt_job(tmp_path: Path) -> None:
    storage, user, _ = _readiness_context(tmp_path)
    storage.write_json(
        user,
        _FILE_ID,
        WorkflowFile.STAGE_THREE_JOB,
        {"schema_version": 2},
        expected_version=None,
    )

    with pytest.raises(HarmonizationNotReadyError, match="cannot be read"):
        require_ready_harmonization_workflow(storage, user, _FILE_ID)


def test_harmonization_readiness_rejects_wrong_file_job(tmp_path: Path) -> None:
    storage, user, plan_version = _readiness_context(tmp_path)
    wrong_file_job = _job_for_readiness(
        plan_version=plan_version,
        status=HarmonizeStatus.SUCCEEDED,
        file_id=_OTHER_FILE_ID,
    )
    storage.write_json(
        user,
        _FILE_ID,
        WorkflowFile.STAGE_THREE_JOB,
        wrong_file_job.to_store(),
        expected_version=None,
    )

    with pytest.raises(HarmonizationNotReadyError, match="cannot be read"):
        require_ready_harmonization_workflow(storage, user, _FILE_ID)


def test_harmonization_readiness_rejects_stale_plan_job(tmp_path: Path) -> None:
    storage, user, _ = _readiness_context(tmp_path)
    save_harmonization_job(
        storage,
        user,
        _job_for_readiness(plan_version="stale-plan", status=HarmonizeStatus.SUCCEEDED),
        expected_version=None,
    )

    with pytest.raises(HarmonizationNotReadyError, match="out of date"):
        require_ready_harmonization_workflow(storage, user, _FILE_ID)


def test_harmonization_readiness_accepts_exact_success(tmp_path: Path) -> None:
    storage, user, plan_version = _readiness_context(tmp_path)
    save_harmonization_job(
        storage,
        user,
        _job_for_readiness(plan_version=plan_version, status=HarmonizeStatus.SUCCEEDED),
        expected_version=None,
    )

    ready = require_ready_harmonization_workflow(storage, user, _FILE_ID)

    assert ready is not None
    assert ready.state.file_id == _FILE_ID


@pytest.mark.parametrize(
    "record",
    [
        {"cde_key": "diagnosis", "cde_id": True},
        {
            "cde_key": "diagnosis",
            "cde_id": 1,
            "alternatives": [{"target": "diagnosis", "confidence": True}],
        },
        {
            "cde_key": "diagnosis",
            "cde_id": 1,
            "alternatives": [{"target": "diagnosis", "confidence": 0.9, "cde_id": True}],
        },
    ],
)
def test_current_mapping_manifest_rejects_boolean_numeric_fields(record: dict[str, object]) -> None:
    with pytest.raises(InvalidMappingManifestError):
        ColumnMappingManifest.from_payload_strict({"column_mappings": {"column_0": record}})


def test_historical_mapping_without_a_source_remains_ai() -> None:
    # Given a historical manifest from before recommendation source was stored.
    manifest = ColumnMappingManifest.from_payload_strict({
        "column_mappings": {"col_0000": {"cde_key": "known"}}
    })

    # When it is decoded, then it keeps the truthful historical default.
    assert manifest.records[column_key_from_string("col_0000")].recommendation_source == RecommendationSource.AI


def test_mapping_choices_reject_an_unknown_unchanged_recommendation() -> None:
    # Given a historical recommendation absent from the selected reference model.
    manifest = ColumnMappingManifest.from_payload_strict({
        "column_mappings": {"col_0000": {"cde_key": "removed"}}
    })
    catalog = CdeCatalog.from_cdes([CDEInfo(None, "known", None, CdeType.PASSTHROUGH)])

    # When Stage 3 applies unchanged choices, then it fails instead of using empty values.
    with pytest.raises(ValueError, match="Unknown CDE key: removed"):
        manifest.apply_choices(ColumnCdeOverrides({}), ColumnRenameSet({}), catalog)
