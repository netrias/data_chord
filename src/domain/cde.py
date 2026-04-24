"""
Common Data Element definitions and column-to-CDE mapping types.

Axis of change: CDE metadata shapes and column-mapping serialization.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict

from netrias_client import Harmonization
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
    confidence: float
    harmonization: Harmonization


class ColumnMappingDecision(TypedDict):
    """Per-column CDE assignment emitted by Stage 2 as part of the harmonization request."""

    column_id: NotRequired[int]  # Optional while accepting stale browser session payloads.
    column_name: str
    cde_name: str | None
    cde_id: int | None
    cde_description: str | None
    method: Literal["ai_recommendation", "user_override"]


class CDEEntry(TypedDict):
    """A column assigned to a CDE; method preserved inside every populated bucket."""

    column_name: str
    cde_name: str
    cde_id: int | None
    cde_description: str | None
    method: Literal["ai_recommendation", "user_override"]


class CDEMappingDocument(TypedDict):
    """Persisted artifact documenting column-to-CDE assignments for a harmonization session."""

    file_id: str
    generated_at: str         # ISO 8601
    schema_name: str          # target standard, e.g. "CDS"
    version_label: str | None  # "1" is the fallback when API is unreachable
    ai_mapped: list[CDEEntry]
    user_overrides: list[CDEEntry]
    pass_through: list[CDEEntry]  # Target CDE identified but values not transformed (numeric / no-PV).
    unmapped_columns: list[str]
