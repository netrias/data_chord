"""
Persist the column-to-CDE mapping artifact included in downloads.

Axis of change: the audit document format for column-keyed CDE mappings.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Literal, NotRequired, TypedDict

import src.domain.dependencies as dependencies
from src.domain.cde import CdeType, ColumnMappingSet, is_rename_only
from src.domain.column_renames import ColumnRenameSet
from src.domain.columns import ColumnKey
from src.domain.data_model_cache import SessionCache
from src.domain.data_model_selection import DataModelSelection
from src.domain.manifest import ColumnMappingManifest, ColumnMappingRecord
from src.domain.storage import FileType


class CdeMappingEntry(TypedDict):
    column_key: str
    source_column_name: str
    output_column_name: str
    cde_key: str | None
    cde_id: NotRequired[int]
    cde_description: NotRequired[str]
    cde_type: NotRequired[str]
    mapping_source: Literal["ai", "user_override", "no_mapping"]
    maps_values: bool


class CdeMappingDocument(TypedDict):
    file_id: str
    generated_at: str
    target_schema: str
    target_version: str
    mappings: list[CdeMappingEntry]


def save_cde_mapping_document(
    file_id: str,
    manifest: ColumnMappingManifest,
    column_mappings: ColumnMappingSet,
    column_renames: ColumnRenameSet,
    cache: SessionCache,
    target_selection: DataModelSelection,
) -> None:
    """Save an audit-friendly mapping plan using the current column-key model."""
    document = CdeMappingDocument(
        file_id=file_id,
        generated_at=datetime.now(UTC).isoformat(),
        target_schema=target_selection.key,
        target_version=target_selection.target_version,
        mappings=_build_entries(manifest, column_mappings, column_renames, cache),
    )
    dependencies.get_file_store().save(file_id, FileType.COLUMN_MAPPING, document)


def load_cde_mapping_json(file_id: str) -> str | None:
    """Return a pretty JSON mapping artifact for the download bundle."""
    payload = dependencies.get_file_store().load(file_id, FileType.COLUMN_MAPPING)
    if payload is None:
        return None
    return json.dumps(payload, indent=2)


def _build_entries(
    manifest: ColumnMappingManifest,
    column_mappings: ColumnMappingSet,
    column_renames: ColumnRenameSet,
    cache: SessionCache,
) -> list[CdeMappingEntry]:
    override_by_key = column_mappings.to_override_map()
    keys = sorted(set(manifest.records) | set(override_by_key) | set(column_renames.renames), key=str)
    return [
        _build_entry(column_key, manifest.records.get(column_key), override_by_key, column_renames, cache)
        for column_key in keys
    ]


def _build_entry(
    column_key: ColumnKey,
    record: ColumnMappingRecord | None,
    overrides: dict[ColumnKey, str | None],
    renames: ColumnRenameSet,
    cache: SessionCache,
) -> CdeMappingEntry:
    cde_key = overrides.get(column_key, record.cde_key if record else None)
    source = _mapping_source(column_key, overrides, cde_key)
    source_name = record.column_name if record and record.column_name else str(column_key)
    output_name = renames.renames.get(column_key, source_name)
    entry = CdeMappingEntry(
        column_key=str(column_key),
        source_column_name=source_name,
        output_column_name=output_name,
        cde_key=cde_key,
        mapping_source=source,
        maps_values=False,
    )
    if cde_key is None:
        return entry

    cde = cache.get_cde_by_key(cde_key)
    cde_type = cde.cde_type if cde else CdeType.PV
    entry["cde_type"] = cde_type.value
    entry["maps_values"] = not is_rename_only(cde_type)
    if cde is not None:
        entry["cde_id"] = cde.cde_id
        if cde.description:
            entry["cde_description"] = cde.description
    elif record is not None:
        entry["cde_id"] = record.cde_id
    return entry


def _mapping_source(
    column_key: ColumnKey,
    overrides: dict[ColumnKey, str | None],
    cde_key: str | None,
) -> Literal["ai", "user_override", "no_mapping"]:
    if cde_key is None:
        return "no_mapping"
    return "user_override" if column_key in overrides else "ai"


__all__ = ["CdeMappingDocument", "CdeMappingEntry", "load_cde_mapping_json", "save_cde_mapping_document"]
