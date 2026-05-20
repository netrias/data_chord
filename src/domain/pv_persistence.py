"""
Disk persistence for PV manifests, enabling cache recovery after server restarts.

Changes when: PV manifest format changes, storage backend changes, or cache recovery logic changes.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import src.domain.dependencies as dependencies
from src.domain.columns import ColumnKey
from src.domain.data_model_cache import SessionCache, get_session_cache
from src.domain.pv_manifest import PVManifest
from src.domain.storage import WorkflowFile, WorkflowNotFoundError

_logger = logging.getLogger(__name__)


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
) -> dict[str, frozenset[str] | None]:
    """Return PV sets by source column key, using cache as an implementation detail."""
    cache = ensure_pvs_loaded(file_id)
    return {str(column_key): cache.get_pvs_for_column(column_key) for column_key in column_keys}


def save_pv_manifest_to_disk(file_id: str, cache: SessionCache, pv_map: dict[str, frozenset[str]]) -> None:
    """Persists PVs so Stage 4/5 can recover after server restart without re-running harmonization."""
    selection = cache.get_model_selection()
    if selection is None:
        _logger.warning("Cannot save PV manifest without data model selection", extra={"file_id": file_id})
        return
    manifest = PVManifest(
        data_model_key=selection.key,
        version_label=selection.version_label,
        column_to_cde_key=cache.get_column_mappings(),
        pvs=pv_map,
    )
    storage = dependencies.get_workflow_storage()
    user = dependencies.get_user_context()
    try:
        existing = storage.read_json(user, file_id, WorkflowFile.PV_MANIFEST)
    except WorkflowNotFoundError:
        storage.create_workflow(user, file_id=file_id)
        existing = None
    storage.write_json(
        user,
        file_id,
        WorkflowFile.PV_MANIFEST,
        manifest.to_store(),
        expected_version=existing.version if existing is not None else None,
    )
    _logger.info("Saved PV manifest to disk", extra={"file_id": file_id})
