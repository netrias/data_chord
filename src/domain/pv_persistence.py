"""
Disk persistence for PV manifests, enabling cache recovery after server restarts.

Changes when: PV manifest format changes, storage backend changes, or cache recovery logic changes.
"""

from __future__ import annotations

import logging
from typing import Any

from src.domain.column_assignment import (
    assignments_to_snapshots,
    legacy_mappings_to_assignments,
    snapshots_to_assignments,
)
from src.domain.data_model_cache import SessionCache, get_session_cache
from src.domain.dependencies import get_file_store
from src.domain.storage import FileType

_logger = logging.getLogger(__name__)


def load_pv_manifest_from_disk(file_id: str, cache: SessionCache) -> None:
    """Server restarts clear in-memory cache; disk manifest enables recovery without re-running Stage 3."""
    store = get_file_store()
    manifest_data: dict[str, Any] | None = store.load(file_id, FileType.PV_MANIFEST)
    if manifest_data is None:
        _logger.debug("No PV manifest found on disk", extra={"file_id": file_id})
        return

    assignments = snapshots_to_assignments(manifest_data.get("column_assignments"))
    if not assignments:
        assignments = legacy_mappings_to_assignments(manifest_data.get("column_to_cde_key", {}))
        if manifest_data.get("column_to_cde_key") and not assignments:
            _logger.warning("Failed to load legacy column_to_cde_key manifest", extra={"file_id": file_id})
    cache.set_column_assignments(assignments)

    pvs_raw = manifest_data.get("pvs", {})
    pv_map = {k: frozenset(v) for k, v in pvs_raw.items()}
    cache.set_pvs_batch(pv_map)

    _logger.info(
        "Loaded PV manifest from disk into cache",
        extra={"file_id": file_id, "column_count": len(assignments), "cde_count": len(pv_map)},
    )


def ensure_pvs_loaded(file_id: str) -> SessionCache:
    """Single entry point for stages needing PV data; handles cache-miss recovery transparently."""
    cache = get_session_cache(file_id)
    if not cache.has_any_pvs():
        load_pv_manifest_from_disk(file_id, cache)
    return cache


def save_pv_manifest_to_disk(file_id: str, cache: SessionCache, pv_map: dict[str, frozenset[str]]) -> None:
    """Persists PVs so Stage 4/5 can recover after server restart without re-running harmonization."""
    data_model_key, version_label = cache.get_model_info()
    store = get_file_store()
    manifest_data = {
        "data_model_key": data_model_key,
        "version_label": version_label,
        "column_assignments": assignments_to_snapshots(cache.get_column_assignments()),
        # Sorted for deterministic JSON output and human-readable diffs
        "pvs": {k: sorted(v) for k, v in pv_map.items()},
    }
    store.save(file_id, FileType.PV_MANIFEST, manifest_data)
    _logger.info("Saved PV manifest to disk", extra={"file_id": file_id})
