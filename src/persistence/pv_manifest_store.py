"""
Disk persistence for PV manifests, enabling cache recovery after server restarts.

Changes when: PV manifest format changes, storage backend changes, or cache recovery logic changes.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

import src.app.dependencies as dependencies
from src.app.session_cache import SessionCache, get_session_cache
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.columns import ColumnKey, column_key_from_string
from src.domain.dataset_workflow_ids import DatasetWorkflowId, dataset_workflow_id_from_value
from src.domain.pv_manifest import PVManifest
from src.storage import WorkflowFile, WorkflowNotFoundError

_logger = logging.getLogger(__name__)


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


def load_pv_manifest_from_disk(file_id: str, cache: SessionCache) -> None:
    """Server restarts clear in-memory cache; disk manifest enables recovery without re-running Stage 3."""
    try:
        stored = dependencies.get_workflow_storage().read_json(
            dependencies.get_user_context(),
            file_id,
            WorkflowFile.PV_MANIFEST,
        )
    except WorkflowNotFoundError:
        stored = None
    manifest = PVManifest.from_store(stored.data) if stored is not None else None
    if manifest is None:
        _logger.debug("No PV manifest found on disk", extra={"file_id": file_id})
        return

    cache.set_column_mappings(manifest.column_to_cde_key)
    cache.set_pvs_batch(manifest.pvs)

    _logger.info(
        "Loaded PV manifest from disk into cache",
        extra={
            "file_id": file_id,
            "column_count": len(manifest.column_to_cde_key.mappings),
            "cde_count": len(manifest.pvs),
        },
    )


def ensure_pvs_loaded(file_id: str) -> SessionCache:
    """Single entry point for stages needing PV data; handles cache-miss recovery transparently."""
    cache = get_session_cache(file_id)
    if not cache.has_any_pvs():
        load_pv_manifest_from_disk(file_id, cache)
    return cache


def column_pv_sets(
    file_id: str,
    column_keys: Iterable[ColumnKey | str],
) -> ColumnPvSets:
    """Return PV sets by source column key, using cache as an implementation detail."""
    requested_column_keys = [str(column_key) for column_key in column_keys]
    cache = ensure_pvs_loaded(file_id)
    if not _cache_can_resolve_columns(cache, requested_column_keys):
        # A cache can contain PV values without the column mapping after partial
        # warmup; reload the manifest so lookups stay column-key based.
        load_pv_manifest_from_disk(file_id, cache)
    return ColumnPvSets({
        column_key_from_string(column_key): cache.get_pvs_for_column(column_key)
        for column_key in requested_column_keys
    })


def _cache_can_resolve_columns(cache: SessionCache, column_keys: Iterable[str]) -> bool:
    """Return false when PVs exist but column->CDE mappings were lost from cache."""
    mappings = cache.get_column_mappings().to_strings()
    pvs_by_cde = cache.get_all_pvs()
    for column_key in column_keys:
        cde_key = mappings.get(column_key)
        if cde_key is None or cde_key not in pvs_by_cde:
            return False
    return True


def save_pv_manifest_to_disk(
    file_id: DatasetWorkflowId | str,
    cache: SessionCache,
    pv_map: CdePvCatalog | Mapping[str, frozenset[str]],
) -> None:
    """Persists PVs so Stage 4/5 can recover after server restart without re-running harmonization."""
    data_model_version = cache.get_data_model_version()
    if data_model_version is None:
        _logger.warning("Cannot save PV manifest without data model version", extra={"file_id": file_id})
        return
    manifest = PVManifest(
        data_model_key=data_model_version.data_model_key,
        external_version_number=data_model_version.external_version_number,
        column_to_cde_key=cache.get_column_mappings(),
        pvs=pv_map if isinstance(pv_map, CdePvCatalog) else CdePvCatalog.from_mapping(pv_map),
    )
    storage = dependencies.get_workflow_storage()
    user = dependencies.get_user_context()
    try:
        existing = storage.read_json(user, file_id, WorkflowFile.PV_MANIFEST)
    except WorkflowNotFoundError:
        storage.create_workflow(user, dataset_workflow_id_from_value(file_id))
        existing = None
    storage.write_json(
        user,
        file_id,
        WorkflowFile.PV_MANIFEST,
        manifest.to_store(),
        expected_version=existing.version if existing is not None else None,
    )
    _logger.info("Saved PV manifest to disk", extra={"file_id": file_id})


__all__ = [
    "ColumnPvSets",
    "column_pv_sets",
    "ensure_pvs_loaded",
    "load_pv_manifest_from_disk",
    "save_pv_manifest_to_disk",
]
