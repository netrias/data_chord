"""
Common Data Element definitions and column-to-CDE mapping types.

Axis of change: CDE metadata shapes and column-mapping serialization.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel

from src.domain.columns import ColumnKey, column_key_from_string

NO_MAPPING_SENTINEL = "No Mapping"


class CdeType(StrEnum):
    """How a CDE validates incoming column values.

    The Stage 2 takeover branches on this in three places: the picker badge,
    the conform pill wording, and the right-pane content (PV list vs. type
    explanatory card). Owner of assignment: ``cde_type_overrides.classify_cde``.
    """

    PV = "pv"                  # value must equal one of the CDE's permissible values
    NUMERIC = "numeric"        # value must parse as a number; no PV list
    PASSTHROUGH = "passthrough"  # value is stored as-is; no validation


def is_rename_only(cde_type: CdeType) -> bool:
    """Numeric and pass-through CDEs only rename columns; they do not map values."""
    return cde_type != CdeType.PV


@dataclass(frozen=True)
class DataModelVersionInfo:
    version_label: str
    version_number: int
    external_version_number: str | None = None
    is_default: bool = False


@dataclass(frozen=True)
class DataModelSummary:
    key: str
    label: str
    versions: list[DataModelVersionInfo]


@dataclass(frozen=True)
class CDEInfo:
    """Dynamic CDE metadata fetched from the Data Model Store API."""

    cde_id: int
    cde_key: str
    description: str | None
    version_label: str
    cde_type: CdeType = field(default=CdeType.PV)


class ModelSuggestion(BaseModel):
    target: str
    similarity: float


def normalize_cde_key(selection: str | None) -> str | None:
    """User dropdown selections may include leading/trailing whitespace from HTML rendering."""
    if not selection:
        return None
    cleaned = selection.strip()
    if not cleaned or cleaned == NO_MAPPING_SENTINEL:
        return None
    return cleaned


@dataclass(frozen=True)
class ColumnMapping:
    column_key: ColumnKey
    cde_key: str | None  # None means "No Mapping" selected


@dataclass(frozen=True)
class ColumnMappingSet:
    mappings: tuple[ColumnMapping, ...]

    @classmethod
    def from_dict(cls, overrides: Mapping[str, str | None]) -> ColumnMappingSet:
        mappings: list[ColumnMapping] = []
        for column_key, selection in overrides.items():
            cde_key = normalize_cde_key(selection)
            mappings.append(ColumnMapping(column_key=column_key_from_string(column_key), cde_key=cde_key))
        return cls(mappings=tuple(mappings))

    def to_dict(self) -> dict[str, str | None]:
        return {str(m.column_key): m.cde_key for m in self.mappings}

    def to_override_map(self) -> dict[ColumnKey, str | None]:
        return {m.column_key: m.cde_key for m in self.mappings}

    def get_applied(self) -> list[ColumnMapping]:
        return [m for m in self.mappings if m.cde_key is not None]

    def get_skipped(self) -> list[ColumnKey]:
        return [m.column_key for m in self.mappings if m.cde_key is None]
