"""
Persist the column-to-CDE mapping artifact included in downloads.

Axis of change: the audit document format for column-keyed CDE mappings.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final, Literal, NotRequired, TypedDict

import src.domain.dependencies as dependencies
from src.domain.cde import CdeType, is_rename_only
from src.domain.column_cde_map import ColumnCdeOverrides
from src.domain.column_renames import ColumnRenameSet
from src.domain.columns import ColumnKey
from src.domain.data_model_cache import SessionCache
from src.domain.data_model_selection import DataModelSelection
from src.domain.manifest import ColumnMappingManifest, ColumnMappingRecord
from src.domain.storage import UserContext, WorkflowFile, WorkflowNotFoundError, WorkflowStorage

MAPPING_SOURCE_AI: Final = "ai"
MAPPING_SOURCE_USER_OVERRIDE: Final = "user_override"
MAPPING_SOURCE_NO_MAPPING: Final = "no_mapping"
MappingSource = Literal["ai", "user_override", "no_mapping"]

MAPPING_ARTIFACT_FIELD_CDE_DESCRIPTION: Final = "cde_description"
MAPPING_ARTIFACT_FIELD_CDE_ID: Final = "cde_id"
MAPPING_ARTIFACT_FIELD_CDE_TYPE: Final = "cde_type"
MAPPING_ARTIFACT_FIELD_MAPS_VALUES: Final = "maps_values"


class CdeMappingEntry(TypedDict):
    """Download artifact row explaining how one source column maps to a CDE.

    This is not the SDK manifest. It is an audit-friendly document for users,
    so it includes source/output column names, mapping source, and whether the
    selected CDE actually maps values or only renames the column.
    """

    column_key: str
    source_column_name: str
    output_column_name: str
    cde_key: str | None
    cde_id: NotRequired[int]
    cde_description: NotRequired[str]
    cde_type: NotRequired[str]
    mapping_source: MappingSource
    maps_values: bool


class CdeMappingDocument(TypedDict):
    """Top-level JSON artifact included in the Stage 5 download bundle."""

    file_id: str
    generated_at: str
    target_schema: str
    target_version: str
    mappings: list[CdeMappingEntry]


def save_cde_mapping_document(
    file_id: str,
    manifest: ColumnMappingManifest,
    column_overrides: ColumnCdeOverrides,
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
        mappings=_build_entries(manifest, column_overrides, column_renames, cache),
    )
    storage = dependencies.get_workflow_storage()
    user = dependencies.get_user_context()
    try:
        existing = storage.read_json(user, file_id, WorkflowFile.CDE_MAPPING)
    except WorkflowNotFoundError:
        storage.create_workflow(user, file_id=file_id)
        existing = None
    storage.write_json(
        user,
        file_id,
        WorkflowFile.CDE_MAPPING,
        document,
        expected_version=existing.version if existing is not None else None,
    )


def load_cde_mapping_json(
    file_id: str,
    workflow_storage: WorkflowStorage | None = None,
    user: UserContext | None = None,
) -> str | None:
    """Return a pretty JSON mapping artifact for the download bundle."""
    storage = workflow_storage if workflow_storage is not None else dependencies.get_workflow_storage()
    context = user if user is not None else dependencies.get_user_context()
    try:
        stored = storage.read_json(context, file_id, WorkflowFile.CDE_MAPPING)
    except WorkflowNotFoundError:
        return None
    if stored is None:
        return None
    return json.dumps(stored.data, indent=2)


def _build_entries(
    manifest: ColumnMappingManifest,
    column_overrides: ColumnCdeOverrides,
    column_renames: ColumnRenameSet,
    cache: SessionCache,
) -> list[CdeMappingEntry]:
    override_by_key = column_overrides.overrides
    keys = sorted(set(manifest.records) | set(override_by_key) | set(column_renames.renames), key=str)
    return [
        _build_entry(column_key, manifest.records.get(column_key), override_by_key, column_renames, cache)
        for column_key in keys
    ]


def _build_entry(
    column_key: ColumnKey,
    record: ColumnMappingRecord | None,
    overrides: Mapping[ColumnKey, str | None],
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
    entry[MAPPING_ARTIFACT_FIELD_CDE_TYPE] = cde_type.value
    entry[MAPPING_ARTIFACT_FIELD_MAPS_VALUES] = not is_rename_only(cde_type)
    if cde is not None:
        entry[MAPPING_ARTIFACT_FIELD_CDE_ID] = cde.cde_id
        if cde.description:
            entry[MAPPING_ARTIFACT_FIELD_CDE_DESCRIPTION] = cde.description
    elif record is not None:
        entry[MAPPING_ARTIFACT_FIELD_CDE_ID] = record.cde_id
    return entry


def _mapping_source(
    column_key: ColumnKey,
    overrides: Mapping[ColumnKey, str | None],
    cde_key: str | None,
) -> MappingSource:
    if cde_key is None:
        return MAPPING_SOURCE_NO_MAPPING
    return MAPPING_SOURCE_USER_OVERRIDE if column_key in overrides else MAPPING_SOURCE_AI


__all__ = [
    "CdeMappingDocument",
    "CdeMappingEntry",
    "MAPPING_ARTIFACT_FIELD_CDE_DESCRIPTION",
    "MAPPING_ARTIFACT_FIELD_CDE_ID",
    "MAPPING_ARTIFACT_FIELD_CDE_TYPE",
    "MAPPING_ARTIFACT_FIELD_MAPS_VALUES",
    "MAPPING_SOURCE_AI",
    "MAPPING_SOURCE_NO_MAPPING",
    "MAPPING_SOURCE_USER_OVERRIDE",
    "MappingSource",
    "load_cde_mapping_json",
    "save_cde_mapping_document",
]
