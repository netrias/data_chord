"""Behavior proof for durable, plan-bound permissible-value snapshots."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.app.session_cache import ReferenceDataVersionMismatchError, SessionCache, clear_all_session_caches
from src.domain.cde import CDEInfo
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.dataset_workflow_ids import dataset_workflow_id_from_string
from src.domain.manifest import ColumnMappingManifest
from src.domain.pv_manifest import PVManifest, PvManifestSchemaError
from src.domain.workflow_state import ConfirmedMappingChoices, WorkflowState
from src.persistence.pv_manifest_store import (
    PvSnapshotMismatchError,
    column_pv_sets,
    save_pv_snapshot,
)
from src.persistence.workflow_state_store import (
    load_workflow_state,
    save_confirmed_mapping_choices_to_state,
    save_initial_workflow_state,
)
from src.storage import LocalWorkflowStorage, UserContext, WorkflowAccessDeniedError

FILE_ID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
MODEL_A = DataModelVersionReference("cptac", "11.0.4")
MODEL_B = DataModelVersionReference("cptac", "12.0.0")


def _manifest() -> ColumnMappingManifest:
    return ColumnMappingManifest.from_payload_strict({
        "column_mappings": {
            "col_0000": {"cde_key": "diagnosis", "cde_id": 1},
            "col_0001": {"cde_key": "notes", "cde_id": 2},
        }
    })


def _stored_workflow(tmp_path: Path) -> tuple[LocalWorkflowStorage, UserContext]:
    storage = LocalWorkflowStorage(tmp_path / "workflow-storage")
    user = UserContext(user_id="alice")
    storage.create_workflow(user, dataset_workflow_id_from_string(FILE_ID))
    return storage, user


def _initial_state(storage: LocalWorkflowStorage, user: UserContext):
    return save_initial_workflow_state(
        storage,
        user,
        WorkflowState.from_data_model_version(FILE_ID, MODEL_A, _manifest()),
    )


def test_pv_manifest_round_trip_uses_current_boundary_schema() -> None:
    manifest = PVManifest(
        data_model_version=MODEL_A,
        workflow_state_version="workflow-state-v1",
        pvs=CdePvCatalog.from_mapping({"diagnosis": frozenset({"Glioma"})}),
    )

    stored = manifest.to_store()

    assert stored == {
        "schema_version": 2,
        "data_model_key": "cptac",
        "external_version_number": "11.0.4",
        "workflow_state_version": "workflow-state-v1",
        "pvs": {"diagnosis": ["Glioma"]},
    }
    assert PVManifest.from_store(stored) == manifest


def test_pv_manifest_rejects_old_boundary_schema() -> None:
    legacy_stored = {
        "schema_version": 1,
        "data_model_key": MODEL_A.data_model_key,
        "external_version_number": MODEL_A.external_version_number,
        "workflow_state_version": "legacy-workflow-state",
        "column_to_cde_key": {"col_0000": "diagnosis"},
        "pvs": {"diagnosis": ["Glioma"]},
    }

    with pytest.raises(PvManifestSchemaError, match="not supported"):
        PVManifest.from_store(legacy_stored)


@pytest.mark.parametrize("schema_version", [2.0, 3, None])
def test_pv_manifest_rejects_non_current_boundary_schema(schema_version: object) -> None:
    with pytest.raises(PvManifestSchemaError, match="not supported"):
        PVManifest.from_store({"schema_version": schema_version})


def test_stage_four_recovers_pvs_after_process_cache_loss(tmp_path: Path) -> None:
    """A process restart does not remove PV dropdown and conformance data."""
    storage, user = _stored_workflow(tmp_path)
    loaded = _initial_state(storage, user)
    save_pv_snapshot(
        storage,
        user,
        loaded,
        CdePvCatalog.from_mapping({
            "diagnosis": frozenset({"Adenocarcinoma", "Glioma"}),
            "notes": frozenset(),
        }),
    )

    clear_all_session_caches()
    reloaded = load_workflow_state(storage, user, FILE_ID)
    assert reloaded is not None

    by_column = column_pv_sets(storage, user, reloaded, ["col_0000", "col_0001"])

    assert by_column.get("col_0000") == frozenset({"Adenocarcinoma", "Glioma"})
    assert by_column.get("col_0001") == frozenset()


def test_missing_snapshot_is_distinct_from_fetched_empty(tmp_path: Path) -> None:
    storage, user = _stored_workflow(tmp_path)
    loaded = _initial_state(storage, user)

    before_fetch = column_pv_sets(storage, user, loaded, ["col_0001"])
    assert before_fetch.get("col_0001") is None

    save_pv_snapshot(storage, user, loaded, CdePvCatalog.from_mapping({"notes": frozenset()}))
    after_fetch = column_pv_sets(storage, user, loaded, ["col_0001"])
    assert after_fetch.get("col_0001") == frozenset()


def test_snapshot_from_stale_workflow_revision_is_rejected(tmp_path: Path) -> None:
    storage, user = _stored_workflow(tmp_path)
    loaded = _initial_state(storage, user)
    save_pv_snapshot(
        storage,
        user,
        loaded,
        CdePvCatalog.from_mapping({"diagnosis": frozenset({"Glioma"})}),
    )

    newer = save_confirmed_mapping_choices_to_state(
        storage,
        user,
        FILE_ID,
        ConfirmedMappingChoices.from_raw({"col_0000": "diagnosis"}, {}),
    )

    with pytest.raises(PvSnapshotMismatchError):
        column_pv_sets(storage, user, newer, ["col_0000"])


def test_cross_owner_cannot_read_pv_snapshot(tmp_path: Path) -> None:
    storage, alice = _stored_workflow(tmp_path)
    loaded = _initial_state(storage, alice)
    save_pv_snapshot(
        storage,
        alice,
        loaded,
        CdePvCatalog.from_mapping({"diagnosis": frozenset({"Glioma"})}),
    )

    bob = UserContext(user_id="bob")
    with pytest.raises(WorkflowAccessDeniedError):
        load_workflow_state(storage, bob, FILE_ID)


def test_model_switch_clears_pvs_and_rejects_late_fetch() -> None:
    cache = SessionCache()
    catalog = CdeCatalog.from_cdes([CDEInfo(cde_id=1, cde_key="diagnosis", description=None)])
    cache.install_reference_data(
        MODEL_A,
        catalog,
        CdePvCatalog.from_mapping({"diagnosis": frozenset({"Glioma"})}),
    )

    cache.set_cde_catalog(
        catalog,
        data_model_key=MODEL_B.data_model_key,
        external_version_number=MODEL_B.external_version_number,
    )

    assert cache.get_all_pvs().values == {}
    with pytest.raises(ReferenceDataVersionMismatchError):
        cache.set_pvs_batch(
            CdePvCatalog.from_mapping({"diagnosis": frozenset({"Stale"})}),
            expected_version=MODEL_A,
        )
