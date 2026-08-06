"""Malformed durable state must fail closed at typed domain boundaries."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from src.domain.dataset_workflow_ids import dataset_workflow_id_from_string
from src.domain.harmonization import HarmonizationManifestSummary
from src.domain.manifest import ColumnMappingManifest, InvalidMappingManifestError
from src.domain.pv_manifest import PVManifest, PvManifestSchemaError
from src.domain.workflow_state import WorkflowState, WorkflowStateSchemaError
from src.stage_3_harmonize.job_state import StageThreeJobState, StageThreeJobUnreadableError

_FILE_ID = dataset_workflow_id_from_string("abcdef0123456789abcdef0123456789")


def test_workflow_state_rejects_boolean_schema_version() -> None:
    with pytest.raises(WorkflowStateSchemaError, match="invalid schema version"):
        WorkflowState.from_store({"schema_version": True}, _FILE_ID)


def test_pv_manifest_rejects_boolean_schema_version() -> None:
    with pytest.raises(PvManifestSchemaError, match="invalid schema version"):
        PVManifest.from_store({"schema_version": True})


def test_stage_three_job_rejects_boolean_schema_version() -> None:
    with pytest.raises(StageThreeJobUnreadableError, match="invalid schema version"):
        StageThreeJobState.from_store({"schema_version": True})


def test_stage_three_job_summary_round_trips_without_an_api_model() -> None:
    job = replace(
        StageThreeJobState.queued(
            polling_job_id="job-1",
            file_id=str(_FILE_ID),
            plan_version="plan-1",
            worker_id="worker-1",
            next_stage_url="/stage-4",
            now=datetime(2026, 8, 6, tzinfo=UTC),
        ),
        manifest_summary=HarmonizationManifestSummary(
            total_terms=3,
            changed_terms=2,
            high_confidence_count=1,
            medium_confidence_count=1,
            low_confidence_count=1,
        ),
    )

    loaded = StageThreeJobState.from_store(job.to_store())

    assert loaded == job
    assert job.to_store()["manifest_summary"] == {
        "total_terms": 3,
        "changed_terms": 2,
        "high_confidence_count": 1,
        "medium_confidence_count": 1,
        "low_confidence_count": 1,
        "non_conformant_terms": 0,
        "column_breakdowns": [],
    }


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
