"""Test-only helpers for inspecting cache internals without widening production APIs."""

from __future__ import annotations

from pathlib import Path

from src.domain.column_assignment import ColumnAssignment
from src.domain.data_model_cache import SessionCache
from src.domain.storage import UploadStorage


def cache_assignment(cache: SessionCache, column_id: int) -> ColumnAssignment | None:
    return cache.get_column_assignments().get(column_id)


def cache_cde_key(cache: SessionCache, column_id: int) -> str | None:
    assignment = cache_assignment(cache, column_id)
    return assignment.cde_key if assignment is not None else None


def cache_pvs_for_cde(cache: SessionCache, cde_key: str) -> frozenset[str] | None:
    return cache.pvs.get(cde_key)


def set_cache_pvs(cache: SessionCache, cde_key: str, values: frozenset[str]) -> None:
    cache.set_pvs_batch({cde_key: values})


def storage_manifest_path(storage: UploadStorage, file_id: str) -> Path:
    return storage.load_harmonization_manifest_path(file_id) or (
        storage._base_dir / "manifests" / f"{file_id}_harmonization.parquet"
    )
