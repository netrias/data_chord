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
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError, field_validator

from src.domain.cde import CdeType, is_rename_only
from src.domain.cde_catalog import CdeCatalog
from src.domain.column_cde_map import ColumnCdeOverrides
from src.domain.column_renames import ColumnRenameSet
from src.domain.columns import ColumnKey, column_key_from_string
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.dataset_workflow_ids import DatasetWorkflowId, dataset_workflow_id_from_value
from src.domain.manifest import ColumnMappingManifest, ColumnMappingRecord, RecommendationSource
from src.domain.tabular_column_renames import ResolvedTabularColumn
from src.storage import (
    StoredJson,
    UserContext,
    VersionToken,
    WorkflowFile,
    WorkflowJsonUnreadableError,
    WorkflowNotFoundError,
    WorkflowStorage,
)


class CdeMappingUnreadableError(Exception):
    """Raised when the durable column mapping document cannot be decoded."""


class MappingSource(StrEnum):
    """How a source column ended up with its target CDE mapping."""

    AI = "ai"
    VALUE_OVERLAP = "value_overlap"
    USER_OVERRIDE = "user_override"
    NO_MAPPING = "no_mapping"


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

    model_config = ConfigDict(extra="forbid")

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


class CdeMappingDocumentStore(BaseModel):
    """Persisted JSON shape for the CDE mapping audit artifact."""

    model_config = ConfigDict(extra="forbid")

    file_id: StrictStr = Field(min_length=1)
    generated_at: StrictStr = Field(min_length=1)
    data_model_key: StrictStr = Field(min_length=1)
    external_version_number: StrictStr = Field(min_length=1)
    mappings: list[CdeMappingEntryStore]

    @field_validator("file_id")
    @classmethod
    def _parse_file_id(cls, value: str) -> str:
        dataset_workflow_id_from_value(value)
        return value

    @field_validator("mappings", mode="before")
    @classmethod
    def _parse_mappings(cls, value: object) -> list[CdeMappingEntryStore]:
        if not isinstance(value, list):
            raise ValueError("mappings must be a list")
        return value

    @field_validator("generated_at")
    @classmethod
    def _parse_generated_at(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("generated_at must be an ISO date and time") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("generated_at must include a time zone")
        return value

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
    def from_store(cls, payload: object) -> Self:
        try:
            return cls.model_validate(payload)
        except ValidationError as exc:
            raise CdeMappingUnreadableError("CDE mapping document has an invalid schema") from exc

    def to_store(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude_none=True)


def save_cde_mapping_document(
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: DatasetWorkflowId | str,
    manifest: ColumnMappingManifest,
    column_overrides: ColumnCdeOverrides,
    column_renames: ColumnRenameSet,
    columns: Sequence[ResolvedTabularColumn],
    catalog: CdeCatalog,
    data_model_version: DataModelVersionReference,
    *,
    expected_version: VersionToken | None,
) -> None:
    """Save an audit-friendly mapping plan using the current column-key model."""
    dataset_workflow_id = dataset_workflow_id_from_value(file_id)
    document = CdeMappingDocument(
        dataset_workflow_id=dataset_workflow_id,
        generated_at=datetime.now(UTC),
        data_model_key=data_model_version.data_model_key,
        external_version_number=data_model_version.external_version_number,
        mappings=_build_entries(manifest, column_overrides, column_renames, columns, catalog),
    )
    workflow_storage.write_json(
        user,
        dataset_workflow_id,
        WorkflowFile.CDE_MAPPING,
        document.to_store(),
        expected_version=expected_version,
    )


def load_cde_mapping_json(
    file_id: str,
    workflow_storage: WorkflowStorage,
    user: UserContext,
) -> str | None:
    """Return a pretty JSON mapping artifact for the download bundle."""
    try:
        stored = _read_cde_mapping(workflow_storage, user, file_id)
    except WorkflowNotFoundError:
        return None
    if stored is None:
        return None
    _cde_mapping_entries_by_column(stored.data, expected_file_id=file_id)
    return json.dumps(stored.data, indent=2)


def load_cde_mapping_entries_by_column(
    file_id: DatasetWorkflowId,
    workflow_storage: WorkflowStorage,
    user: UserContext,
) -> Mapping[ColumnKey, CdeMappingEntry]:
    """Load the mapping artifact as app types keyed by canonical source column identity."""
    try:
        stored = _read_cde_mapping(workflow_storage, user, file_id)
    except WorkflowNotFoundError:
        return {}
    if stored is None:
        return {}
    return _cde_mapping_entries_by_column(stored.data, expected_file_id=str(file_id))


def _cde_mapping_entries_by_column(
    payload: object,
    *,
    expected_file_id: str,
) -> Mapping[ColumnKey, CdeMappingEntry]:
    document = CdeMappingDocumentStore.from_store(payload)
    try:
        document_file_id = dataset_workflow_id_from_value(document.file_id)
    except (TypeError, ValueError) as exc:
        raise CdeMappingUnreadableError("CDE mapping document has an invalid workflow identity") from exc
    if str(document_file_id) != expected_file_id:
        raise CdeMappingUnreadableError("CDE mapping document belongs to another workflow")

    result: dict[ColumnKey, CdeMappingEntry] = {}
    for stored_entry in document.mappings:
        try:
            entry = stored_entry.to_domain()
        except (TypeError, ValueError) as exc:
            raise CdeMappingUnreadableError("CDE mapping document has an invalid entry") from exc
        if entry.column_key in result:
            raise CdeMappingUnreadableError("CDE mapping document has duplicate column identities")
        result[entry.column_key] = entry
    return result


def _read_cde_mapping(
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: DatasetWorkflowId | str,
) -> StoredJson | None:
    try:
        return workflow_storage.read_json(user, str(file_id), WorkflowFile.CDE_MAPPING)
    except WorkflowJsonUnreadableError as exc:
        raise CdeMappingUnreadableError(file_id) from exc


def _build_entries(
    manifest: ColumnMappingManifest,
    column_overrides: ColumnCdeOverrides,
    column_renames: ColumnRenameSet,
    columns: Sequence[ResolvedTabularColumn],
    catalog: CdeCatalog,
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
            catalog,
        )
        for column_key in keys
    ]


def _build_entry(
    column_key: ColumnKey,
    record: ColumnMappingRecord | None,
    overrides: Mapping[ColumnKey, str | None],
    renames: ColumnRenameSet,
    column: ResolvedTabularColumn | None,
    catalog: CdeCatalog,
) -> CdeMappingEntry:
    cde_key = overrides.get(column_key, record.cde_key if record else None)
    source = _mapping_source(column_key, overrides, cde_key, record)
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

    cde = catalog.get(cde_key)
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
    record: ColumnMappingRecord | None,
) -> MappingSource:
    if cde_key is None:
        return MappingSource.NO_MAPPING
    if column_key in overrides:
        return MappingSource.USER_OVERRIDE
    if record is not None and record.recommendation_source == RecommendationSource.VALUE_OVERLAP:
        return MappingSource.VALUE_OVERLAP
    return MappingSource.AI


__all__ = [
    "CdeMappingUnreadableError",
    "CdeMappingDocument",
    "CdeMappingEntry",
    "MappingSource",
    "load_cde_mapping_entries_by_column",
    "load_cde_mapping_json",
    "save_cde_mapping_document",
]
