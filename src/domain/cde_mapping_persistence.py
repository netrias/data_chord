"""
Persist the column-to-CDE mapping artifact included in downloads.

Axis of change: the audit document format for column-keyed CDE mappings.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError, field_validator

import src.domain.dependencies as dependencies
from src.domain.cde import CdeType, is_rename_only
from src.domain.column_cde_map import ColumnCdeOverrides
from src.domain.column_renames import ColumnRenameSet
from src.domain.columns import ColumnKey, column_key_from_string
from src.domain.data_model_cache import SessionCache
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.dataset_workflow_ids import DatasetWorkflowId, dataset_workflow_id_from_value
from src.domain.manifest import ColumnMappingManifest, ColumnMappingRecord
from src.domain.storage import UserContext, WorkflowFile, WorkflowNotFoundError, WorkflowStorage
from src.domain.tabular_column_renames import ResolvedTabularColumn

MAPPING_SOURCE_AI: Final = "ai"
MAPPING_SOURCE_USER_OVERRIDE: Final = "user_override"
MAPPING_SOURCE_NO_MAPPING: Final = "no_mapping"


class MappingSource(StrEnum):
    """How a source column ended up with its target CDE mapping."""

    AI = MAPPING_SOURCE_AI
    USER_OVERRIDE = MAPPING_SOURCE_USER_OVERRIDE
    NO_MAPPING = MAPPING_SOURCE_NO_MAPPING

MAPPING_ARTIFACT_FIELD_CDE_DESCRIPTION: Final = "cde_description"
MAPPING_ARTIFACT_FIELD_CDE_ID: Final = "cde_id"
MAPPING_ARTIFACT_FIELD_CDE_TYPE: Final = "cde_type"
MAPPING_ARTIFACT_FIELD_MAPS_VALUES: Final = "maps_values"


@dataclass(frozen=True)
class CdeMappingEntry:
    """Download artifact row explaining how one source column maps to a CDE.

    This is not the SDK manifest. It is an audit-friendly document for users,
    so it includes source/output column names, mapping source, and whether the
    selected CDE actually maps values or only renames the column.
    """

    column_key: ColumnKey
    source_column_name: str
    output_column_name: str
    cde_key: str | None
    mapping_source: MappingSource
    maps_values: bool
    cde_id: int | None = None
    cde_description: str | None = None
    cde_type: CdeType | None = None

    def to_store(self) -> dict[str, object]:
        return CdeMappingEntryStore.from_domain(self).to_store()


@dataclass(frozen=True)
class CdeMappingDocument:
    """Top-level JSON artifact included in the Stage 5 download bundle."""

    dataset_workflow_id: DatasetWorkflowId
    generated_at: datetime
    data_model_key: str
    external_version_number: str
    mappings: list[CdeMappingEntry]

    def to_store(self) -> dict[str, object]:
        return CdeMappingDocumentStore.from_domain(self).to_store()


class CdeMappingEntryStore(BaseModel):
    """Persisted JSON shape for one CDE mapping entry."""

    model_config = ConfigDict(extra="ignore")

    column_key: StrictStr = Field(min_length=1)
    source_column_name: StrictStr
    output_column_name: StrictStr
    cde_key: StrictStr | None = None
    cde_id: StrictInt | None = None
    cde_description: StrictStr | None = None
    cde_type: CdeType | None = None
    mapping_source: MappingSource
    maps_values: StrictBool

    @classmethod
    def from_domain(cls, entry: CdeMappingEntry) -> Self:
        return cls(
            column_key=str(entry.column_key),
            source_column_name=entry.source_column_name,
            output_column_name=entry.output_column_name,
            cde_key=entry.cde_key,
            cde_id=entry.cde_id,
            cde_description=entry.cde_description,
            cde_type=entry.cde_type,
            mapping_source=entry.mapping_source,
            maps_values=entry.maps_values,
        )

    def to_domain(self) -> CdeMappingEntry:
        return CdeMappingEntry(
            column_key=column_key_from_string(self.column_key),
            source_column_name=self.source_column_name,
            output_column_name=self.output_column_name,
            cde_key=self.cde_key,
            cde_id=self.cde_id,
            cde_description=self.cde_description,
            cde_type=self.cde_type,
            mapping_source=self.mapping_source,
            maps_values=self.maps_values,
        )

    def to_store(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude_none=True)


class CdeMappingDocumentStore(BaseModel):
    """Persisted JSON shape for the CDE mapping audit artifact."""

    model_config = ConfigDict(extra="ignore")

    file_id: StrictStr | None = None
    generated_at: StrictStr | None = None
    data_model_key: StrictStr | None = None
    external_version_number: StrictStr | None = None
    mappings: list[CdeMappingEntryStore] = Field(default_factory=list)

    @field_validator("mappings", mode="before")
    @classmethod
    def _parse_mappings(cls, value: object) -> list[CdeMappingEntryStore]:
        if not isinstance(value, list):
            return []
        entries: list[CdeMappingEntryStore] = []
        for item in value:
            try:
                entries.append(CdeMappingEntryStore.model_validate(item))
            except ValidationError:
                continue
        return entries

    @classmethod
    def from_domain(cls, document: CdeMappingDocument) -> Self:
        return cls(
            file_id=str(document.dataset_workflow_id),
            generated_at=document.generated_at.isoformat(),
            data_model_key=document.data_model_key,
            external_version_number=document.external_version_number,
            mappings=[CdeMappingEntryStore.from_domain(entry) for entry in document.mappings],
        )

    @classmethod
    def from_store(cls, payload: object) -> Self | None:
        try:
            return cls.model_validate(payload)
        except ValidationError:
            return None

    def to_store(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude_none=True)


def save_cde_mapping_document(
    file_id: DatasetWorkflowId | str,
    manifest: ColumnMappingManifest,
    column_overrides: ColumnCdeOverrides,
    column_renames: ColumnRenameSet,
    columns: Sequence[ResolvedTabularColumn],
    cache: SessionCache,
    data_model_version: DataModelVersionReference,
) -> None:
    """Save an audit-friendly mapping plan using the current column-key model."""
    dataset_workflow_id = dataset_workflow_id_from_value(file_id)
    document = CdeMappingDocument(
        dataset_workflow_id=dataset_workflow_id,
        generated_at=datetime.now(UTC),
        data_model_key=data_model_version.data_model_key,
        external_version_number=data_model_version.external_version_number,
        mappings=_build_entries(manifest, column_overrides, column_renames, columns, cache),
    )
    storage = dependencies.get_workflow_storage()
    user = dependencies.get_user_context()
    try:
        existing = storage.read_json(user, dataset_workflow_id, WorkflowFile.CDE_MAPPING)
    except WorkflowNotFoundError:
        storage.create_workflow(user, dataset_workflow_id)
        existing = None
    storage.write_json(
        user,
        dataset_workflow_id,
        WorkflowFile.CDE_MAPPING,
        document.to_store(),
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


def load_cde_mapping_entries_by_column(
    file_id: DatasetWorkflowId,
    workflow_storage: WorkflowStorage,
    user: UserContext,
) -> Mapping[ColumnKey, CdeMappingEntry]:
    """Load the mapping artifact as app types keyed by canonical source column identity."""
    try:
        stored = workflow_storage.read_json(user, file_id, WorkflowFile.CDE_MAPPING)
    except WorkflowNotFoundError:
        return {}
    if stored is None:
        return {}
    return _cde_mapping_entries_by_column(stored.data)


def _cde_mapping_entries_by_column(payload: object) -> Mapping[ColumnKey, CdeMappingEntry]:
    document = CdeMappingDocumentStore.from_store(payload)
    if document is None:
        return {}

    result: dict[ColumnKey, CdeMappingEntry] = {}
    for stored_entry in document.mappings:
        try:
            entry = stored_entry.to_domain()
        except ValueError:
            continue
        result[entry.column_key] = entry
    return result


def _build_entries(
    manifest: ColumnMappingManifest,
    column_overrides: ColumnCdeOverrides,
    column_renames: ColumnRenameSet,
    columns: Sequence[ResolvedTabularColumn],
    cache: SessionCache,
) -> list[CdeMappingEntry]:
    override_by_key = column_overrides.overrides
    column_by_key = {column.key: column for column in columns}
    # Include rename-only and no-mapping columns too; the download artifact is
    # an audit trail for every output column, not just value-harmonized fields.
    keys = sorted(
        set(column_by_key) | set(manifest.records) | set(override_by_key) | set(column_renames.renames),
        key=str,
    )
    return [
        _build_entry(
            column_key,
            manifest.records.get(column_key),
            override_by_key,
            column_renames,
            column_by_key.get(column_key),
            cache,
        )
        for column_key in keys
    ]


def _build_entry(
    column_key: ColumnKey,
    record: ColumnMappingRecord | None,
    overrides: Mapping[ColumnKey, str | None],
    renames: ColumnRenameSet,
    column: ResolvedTabularColumn | None,
    cache: SessionCache,
) -> CdeMappingEntry:
    cde_key = overrides.get(column_key, record.cde_key if record else None)
    source = _mapping_source(column_key, overrides, cde_key)
    source_name = (
        column.original_name
        if column
        else record.column_name
        if record and record.column_name
        else str(column_key)
    )
    output_name = column.output_name if column else renames.renames.get(column_key, source_name)
    if cde_key is None:
        return CdeMappingEntry(
            column_key=column_key,
            source_column_name=source_name,
            output_column_name=output_name,
            cde_key=None,
            mapping_source=source,
            maps_values=False,
        )

    cde = cache.get_cde_by_key(cde_key)
    cde_type = cde.cde_type if cde else CdeType.PV
    cde_id = None
    cde_description = None
    if cde is not None:
        cde_id = cde.cde_id
        cde_description = cde.description
    elif record is not None:
        cde_id = record.cde_id
    return CdeMappingEntry(
        column_key=column_key,
        source_column_name=source_name,
        output_column_name=output_name,
        cde_key=cde_key,
        mapping_source=source,
        maps_values=not is_rename_only(cde_type),
        cde_id=cde_id,
        cde_description=cde_description,
        cde_type=cde_type,
    )


def _mapping_source(
    column_key: ColumnKey,
    overrides: Mapping[ColumnKey, str | None],
    cde_key: str | None,
) -> MappingSource:
    if cde_key is None:
        return MappingSource.NO_MAPPING
    return MappingSource.USER_OVERRIDE if column_key in overrides else MappingSource.AI


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
    "load_cde_mapping_entries_by_column",
    "load_cde_mapping_json",
    "save_cde_mapping_document",
]
