"""
Common Data Element definitions and column-to-CDE mapping types.

Axis of change: CDE metadata shapes and column-mapping serialization.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import BaseModel

NO_MAPPING_SENTINEL = "No Mapping"


@dataclass(frozen=True)
class DataModelSummary:
    key: str
    label: str
    versions: list[str]


@dataclass(frozen=True)
class CDEInfo:
    """Dynamic CDE metadata fetched from the Data Model Store API."""

    cde_id: int
    cde_key: str
    description: str | None
    version_label: str


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
    column_name: str
    cde_key: str | None  # None means "No Mapping" selected


@dataclass(frozen=True)
class ColumnMappingSet:
    mappings: tuple[ColumnMapping, ...]

    @classmethod
    def from_dict(cls, overrides: Mapping[str, str]) -> ColumnMappingSet:
        mappings: list[ColumnMapping] = []
        for column, selection in overrides.items():
            cde_key = normalize_cde_key(selection)
            mappings.append(ColumnMapping(column_name=column, cde_key=cde_key))
        return cls(mappings=tuple(mappings))

    def to_dict(self) -> dict[str, str | None]:
        return {m.column_name: m.cde_key for m in self.mappings}

    def get_applied(self) -> list[ColumnMapping]:
        return [m for m in self.mappings if m.cde_key is not None]

    def get_skipped(self) -> list[str]:
        return [m.column_name for m in self.mappings if m.cde_key is None]
