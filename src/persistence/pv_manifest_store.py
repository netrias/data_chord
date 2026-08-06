"""Persist and project model-version-bound permissible values."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.column_cde_map import ColumnCdeMap
from src.domain.columns import ColumnKey, column_key_from_string
from src.domain.pv_manifest import PVManifest, PvManifestSchemaError
from src.persistence.workflow_state_store import LoadedWorkflowState
from src.storage import UserContext, WorkflowFile, WorkflowStorage


class PvSnapshotUnreadableError(Exception):
    """Raised when a stored PV snapshot exists but cannot be decoded safely."""


class PvSnapshotMismatchError(Exception):
    """Raised when PVs belong to another workflow plan or model version."""


@dataclass(frozen=True)
class ColumnPvSets:
    """PV sets keyed by stable source column identity."""

    values: Mapping[ColumnKey, frozenset[str] | None]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    def get(self, column_key: ColumnKey | str) -> frozenset[str] | None:
        return self.values.get(column_key_from_string(str(column_key)))

    def to_strings(self) -> dict[str, frozenset[str] | None]:
        return {str(column_key): pv_set for column_key, pv_set in self.values.items()}


def load_pv_snapshot(
    workflow_storage: WorkflowStorage,
    user: UserContext,
    loaded_state: LoadedWorkflowState,
) -> CdePvCatalog | None:
    """Read PVs only after an authorized workflow-state read."""
    state = loaded_state.state
    stored = workflow_storage.read_json(user, state.file_id, WorkflowFile.PV_MANIFEST)
    if stored is None:
        return None
    try:
        manifest = PVManifest.from_store(stored.data)
    except PvManifestSchemaError as exc:
        raise PvSnapshotUnreadableError(state.file_id) from exc
    if manifest is None:
        raise PvSnapshotUnreadableError(state.file_id)
    if manifest.data_model_version != state.data_model_version:
        raise PvSnapshotMismatchError(state.file_id)
    if (
        manifest.workflow_state_version is not None
        and manifest.workflow_state_version != loaded_state.version.value
    ):
        raise PvSnapshotMismatchError(state.file_id)
    return manifest.pvs


def column_pv_sets(
    workflow_storage: WorkflowStorage,
    user: UserContext,
    loaded_state: LoadedWorkflowState,
    column_keys: Iterable[ColumnKey | str],
) -> ColumnPvSets:
    """Project persisted CDE PVs through the canonical workflow mapping."""
    column_cde_map = effective_column_cde_map(loaded_state)
    pv_catalog = load_pv_snapshot(workflow_storage, user, loaded_state)
    return ColumnPvSets({
        column_key_from_string(str(column_key)): _pvs_for_column(
            column_cde_map,
            pv_catalog,
            column_key,
        )
        for column_key in column_keys
    })


def effective_column_cde_map(loaded_state: LoadedWorkflowState) -> ColumnCdeMap:
    state = loaded_state.state
    if state.mapping_manifest is None:
        raise PvSnapshotUnreadableError(state.file_id)
    mappings = state.mapping_manifest.column_cde_map()
    if state.mapping_choices is None:
        return mappings
    return mappings.with_overrides(state.mapping_choices.column_overrides)


def save_pv_snapshot(
    workflow_storage: WorkflowStorage,
    user: UserContext,
    loaded_state: LoadedWorkflowState,
    pv_map: CdePvCatalog | Mapping[str, frozenset[str]],
) -> None:
    """Persist PVs with exact plan identity and an old-reader mapping projection."""
    state = loaded_state.state
    manifest = PVManifest(
        data_model_version=state.data_model_version,
        workflow_state_version=loaded_state.version.value,
        column_to_cde_key=effective_column_cde_map(loaded_state),
        pvs=pv_map if isinstance(pv_map, CdePvCatalog) else CdePvCatalog.from_mapping(pv_map),
    )
    existing = workflow_storage.read_json(user, state.file_id, WorkflowFile.PV_MANIFEST)
    workflow_storage.write_json(
        user,
        state.file_id,
        WorkflowFile.PV_MANIFEST,
        manifest.to_store(),
        expected_version=existing.version if existing is not None else None,
    )


def _pvs_for_column(
    column_cde_map: ColumnCdeMap,
    pv_catalog: CdePvCatalog | None,
    column_key: ColumnKey | str,
) -> frozenset[str] | None:
    if pv_catalog is None:
        return None
    cde_key = column_cde_map.mappings.get(column_key_from_string(str(column_key)))
    return pv_catalog.get(cde_key) if cde_key is not None else None


__all__ = [
    "ColumnPvSets",
    "PvSnapshotMismatchError",
    "PvSnapshotUnreadableError",
    "column_pv_sets",
    "effective_column_cde_map",
    "load_pv_snapshot",
    "save_pv_snapshot",
]
