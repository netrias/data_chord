"""Common Data Element metadata and normalization helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

NO_MAPPING_SENTINEL = "No Mapping"


class CdeType(StrEnum):
    """How a CDE validates incoming column values.

    The Stage 2 takeover branches on this in three places: the picker badge,
    the conform pill wording, and the right-pane content (PV list vs. type
    explanatory card). Owner of assignment: ``cde_type_classification.classify_cde``.
    """

    PV = "pv"                  # value must equal one of the CDE's permissible values
    PASSTHROUGH = "passthrough"  # value is stored as-is; no validation


def is_rename_only(cde_type: CdeType) -> bool:
    """Pass-through CDEs only rename columns; they do not map values."""
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


@dataclass(frozen=True)
class ModelSuggestion:
    """One CDE candidate returned by mapping discovery for a source column."""

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
