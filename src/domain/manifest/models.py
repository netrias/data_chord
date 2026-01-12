"""Harmonization manifest data models and parquet schema."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypedDict

import pyarrow as pa


class ConfidenceBucket(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def label(self) -> str:
        return self.value.title()


HIGH_CONFIDENCE_THRESHOLD: float = 0.8
MEDIUM_CONFIDENCE_THRESHOLD: float = 0.45


class ColumnMappingEntry(TypedDict, total=False):
    route: str
    targetField: str
    cde_id: int


class ManifestPayload(TypedDict, total=False):
    column_mappings: dict[str, dict[str, object]]


@dataclass(frozen=True)
class ManualOverride:
    user_id: str | None
    timestamp: str
    value: str


@dataclass(frozen=True)
class PVAdjustment:
    """Recorded when harmonized value is adjusted to conform to PV set."""

    timestamp: str
    original_harmonization: str
    adjusted_value: str
    source: str
    user_id: str = "pv_adjustment"


@dataclass(frozen=True)
class ManifestRow:
    job_id: str
    column_id: int
    column_name: str
    to_harmonize: str
    top_harmonization: str
    ontology_id: str | None
    top_harmonizations: list[str]
    confidence_score: float | None
    error: str | None
    row_indices: list[int]
    manual_overrides: list[ManualOverride]
    pv_adjustment: PVAdjustment | None = None


@dataclass(frozen=True)
class ManifestSummary:
    total_terms: int
    changed_terms: int
    high_confidence_count: int
    medium_confidence_count: int
    low_confidence_count: int
    rows: list[ManifestRow]


def confidence_bucket(score: float | None) -> ConfidenceBucket:
    if score is None:
        return ConfidenceBucket.LOW
    if score >= HIGH_CONFIDENCE_THRESHOLD:
        return ConfidenceBucket.HIGH
    if score >= MEDIUM_CONFIDENCE_THRESHOLD:
        return ConfidenceBucket.MEDIUM
    return ConfidenceBucket.LOW


COMPLETENESS_HIGH_THRESHOLD: float = 0.8
COMPLETENESS_MEDIUM_THRESHOLD: float = 0.5


def completeness_bucket(non_empty: int, sample_size: int) -> ConfidenceBucket:
    if sample_size == 0:
        return ConfidenceBucket.LOW
    ratio = non_empty / sample_size
    if ratio >= COMPLETENESS_HIGH_THRESHOLD:
        return ConfidenceBucket.HIGH
    if ratio >= COMPLETENESS_MEDIUM_THRESHOLD:
        return ConfidenceBucket.MEDIUM
    return ConfidenceBucket.LOW


def is_value_changed(original: str | None, harmonized: str | None) -> bool:
    """Whitespace is semantically significant in ontological data."""
    harmonized_str = harmonized or ""
    if not harmonized_str.strip():
        return False
    original_str = original or ""
    return original_str != harmonized_str


def get_latest_override_value(overrides: list[ManualOverride]) -> str | None:
    if not overrides:
        return None
    return overrides[-1].value


def get_manifest_schema() -> pa.Schema:
    override_struct = pa.struct([
        ("user_id", pa.string()),
        ("timestamp", pa.string()),
        ("value", pa.string()),
    ])

    pv_adjustment_struct = pa.struct([
        ("timestamp", pa.string()),
        ("original_harmonization", pa.string()),
        ("adjusted_value", pa.string()),
        ("source", pa.string()),
        ("user_id", pa.string()),
    ])

    return pa.schema([
        ("job_id", pa.string()),
        ("column_id", pa.int64()),
        ("column_name", pa.string()),
        ("to_harmonize", pa.string()),
        ("top_harmonization", pa.string()),
        ("ontology_id", pa.string()),
        ("top_harmonizations", pa.list_(pa.string())),
        ("confidence_score", pa.float64()),
        ("error", pa.string()),
        ("row_indices", pa.list_(pa.int64())),
        ("manual_overrides", pa.list_(override_struct)),
        ("pv_adjustment", pv_adjustment_struct),
    ])


__all__ = [
    "COMPLETENESS_HIGH_THRESHOLD",
    "COMPLETENESS_MEDIUM_THRESHOLD",
    "ColumnMappingEntry",
    "ConfidenceBucket",
    "HIGH_CONFIDENCE_THRESHOLD",
    "MEDIUM_CONFIDENCE_THRESHOLD",
    "ManifestPayload",
    "ManifestRow",
    "ManifestSummary",
    "ManualOverride",
    "PVAdjustment",
    "completeness_bucket",
    "confidence_bucket",
    "get_latest_override_value",
    "get_manifest_schema",
    "is_value_changed",
]
