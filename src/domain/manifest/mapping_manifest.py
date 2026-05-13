"""Column-keyed CDE mapping manifests at the app and SDK boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Final

from src.domain.cde import ModelSuggestion
from src.domain.column_cde_map import ColumnCdeMap
from src.domain.columns import ColumnKey, column_key_from_string
from src.domain.manifest.models import AlternativeEntry, ColumnMappingEntry, ManifestPayload

# SDK manifest field names. Keeping these named makes the JSON contract visible
# without spreading string literals through parsing and serialization code.
MANIFEST_FIELD_COLUMN_MAPPINGS: Final = "column_mappings"
MAPPING_FIELD_ALTERNATIVES: Final = "alternatives"
MAPPING_FIELD_CDE_ID: Final = "cde_id"
MAPPING_FIELD_CDE_KEY: Final = "cde_key"
MAPPING_FIELD_COLUMN_NAME: Final = "column_name"
MAPPING_FIELD_CONFIDENCE: Final = "confidence"
MAPPING_FIELD_HARMONIZATION: Final = "harmonization"
MAPPING_FIELD_ROUTE: Final = "route"
MAPPING_FIELD_TARGET: Final = "target"

# The SDK requires this field on each column mapping. It means the column is
# eligible for the harmonization workflow; unmapped/skipped columns are omitted
# from ColumnMappingManifest instead of carrying another harmonization value.
DEFAULT_HARMONIZATION: Final = "harmonizable"


@dataclass(frozen=True)
class MappingAlternative:
    """One ranked CDE candidate returned by mapping discovery.

    The top-level mapping record is the applied/default CDE. Alternatives keep
    the full candidate list so Stage 2 can show every AI recommendation without
    having to understand the raw SDK response shape.
    """

    target: str
    confidence: float
    cde_id: int | None = None
    harmonization: str | None = None

    @classmethod
    def from_payload(cls, payload: object) -> MappingAlternative | None:
        if not isinstance(payload, Mapping):
            return None
        target = payload.get(MAPPING_FIELD_TARGET)
        if not isinstance(target, str) or not target:
            return None
        return cls(
            target=target,
            confidence=_score_from_payload(payload),
            cde_id=_int_or_none(payload.get(MAPPING_FIELD_CDE_ID)),
            harmonization=_str_or_none(payload.get(MAPPING_FIELD_HARMONIZATION)),
        )

    def to_payload(self) -> AlternativeEntry:
        payload = AlternativeEntry(target=self.target, confidence=self.confidence)
        if self.cde_id is not None:
            payload[MAPPING_FIELD_CDE_ID] = self.cde_id
        if self.harmonization is not None:
            payload[MAPPING_FIELD_HARMONIZATION] = self.harmonization
        return payload

    def to_suggestion(self) -> ModelSuggestion:
        return ModelSuggestion(target=self.target, similarity=self.confidence)


@dataclass(frozen=True)
class ColumnMappingRecord:
    """Applied CDE mapping for one stable source column.

    ``column_key`` is the app's immutable identity for the source column.
    ``column_name`` is display/output metadata and may change when the user
    renames a column. This keeps duplicate headers safe while still preserving
    the SDK manifest fields needed for harmonization.
    """

    column_key: ColumnKey
    cde_key: str
    cde_id: int
    column_name: str | None = None
    harmonization: str | None = None
    route: str | None = None
    alternatives: tuple[MappingAlternative, ...] = ()

    @classmethod
    def from_payload(cls, column_key: str, payload: object) -> ColumnMappingRecord | None:
        if not isinstance(payload, Mapping):
            return None
        cde_key = _cde_key_from_payload(payload)
        cde_id = _int_or_none(payload.get(MAPPING_FIELD_CDE_ID))
        if cde_key is None or cde_id is None:
            return None
        alternatives = tuple(
            alternative
            for raw in _list_or_empty(payload.get(MAPPING_FIELD_ALTERNATIVES))
            if (alternative := MappingAlternative.from_payload(raw)) is not None
        )
        return cls(
            column_key=column_key_from_string(column_key),
            cde_key=cde_key,
            cde_id=cde_id,
            column_name=_str_or_none(payload.get(MAPPING_FIELD_COLUMN_NAME)),
            harmonization=_str_or_none(payload.get(MAPPING_FIELD_HARMONIZATION)),
            route=_str_or_none(payload.get(MAPPING_FIELD_ROUTE)),
            alternatives=alternatives,
        )

    def to_payload(self) -> ColumnMappingEntry:
        payload = ColumnMappingEntry(
            column_name=self.column_name or str(self.column_key),
            cde_key=self.cde_key,
            cde_id=self.cde_id,
            harmonization=self.harmonization or DEFAULT_HARMONIZATION,
            alternatives=[alternative.to_payload() for alternative in self.alternatives],
        )
        if self.route is not None:
            payload[MAPPING_FIELD_ROUTE] = self.route
        return payload

    def suggestions(self) -> list[ModelSuggestion]:
        if self.alternatives:
            return [alternative.to_suggestion() for alternative in self.alternatives]
        return [ModelSuggestion(target=self.cde_key, similarity=1.0)]


@dataclass(frozen=True)
class ColumnMappingManifest:
    """Column-keyed view of the SDK mapping manifest.

    This is the canonical in-app representation for AI-selected CDE mappings.
    It accepts raw SDK/browser payloads at the boundary, drops incomplete
    records, and gives the rest of the app typed records keyed by ``ColumnKey``.
    """

    records: dict[ColumnKey, ColumnMappingRecord]

    @classmethod
    def empty(cls) -> ColumnMappingManifest:
        return cls(records={})

    @classmethod
    def from_payload(cls, payload: Mapping[str, object] | object | None) -> ColumnMappingManifest:
        raw_mappings = _column_mappings_from_payload(payload)
        if raw_mappings is None:
            return cls.empty()

        records: dict[ColumnKey, ColumnMappingRecord] = {}
        for raw_key, raw_record in raw_mappings.items():
            if not isinstance(raw_key, str):
                continue
            record = ColumnMappingRecord.from_payload(raw_key, raw_record)
            if record is not None:
                records[record.column_key] = record
        return cls(records=records)

    def to_payload(self) -> ManifestPayload:
        return {
            MANIFEST_FIELD_COLUMN_MAPPINGS: {
                str(column_key): record.to_payload()
                for column_key, record in self.records.items()
            }
        }

    def with_record(self, record: ColumnMappingRecord) -> ColumnMappingManifest:
        records = dict(self.records)
        records[record.column_key] = record
        return ColumnMappingManifest(records)

    def without_column(self, column_key: ColumnKey) -> ColumnMappingManifest:
        records = dict(self.records)
        records.pop(column_key, None)
        return ColumnMappingManifest(records)

    def with_column_names(self, renames: Mapping[ColumnKey, str]) -> ColumnMappingManifest:
        records = dict(self.records)
        for column_key, column_name in renames.items():
            if column_key in records:
                records[column_key] = replace(records[column_key], column_name=column_name)
        return ColumnMappingManifest(records)

    def column_cde_map(self) -> ColumnCdeMap:
        return ColumnCdeMap({column_key: record.cde_key for column_key, record in self.records.items()})

    def suggestions_by_column(self) -> dict[str, list[ModelSuggestion]]:
        return {str(column_key): record.suggestions() for column_key, record in self.records.items()}


def normalize_manifest(payload: Mapping[str, object] | object | None) -> ManifestPayload:
    return ColumnMappingManifest.from_payload(payload).to_payload()


def _column_mappings_from_payload(payload: Mapping[str, object] | object | None) -> Mapping[object, object] | None:
    if not isinstance(payload, Mapping):
        return None
    raw_mappings = payload.get(MANIFEST_FIELD_COLUMN_MAPPINGS)
    return raw_mappings if isinstance(raw_mappings, Mapping) else None


def _cde_key_from_payload(payload: Mapping[object, object]) -> str | None:
    cde_key = payload.get(MAPPING_FIELD_CDE_KEY)
    if isinstance(cde_key, str) and cde_key:
        return cde_key
    return None


def _score_from_payload(payload: Mapping[object, object]) -> float:
    confidence = payload.get(MAPPING_FIELD_CONFIDENCE)
    if isinstance(confidence, (int, float)):
        return float(confidence)
    return 0.0


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _list_or_empty(value: object) -> list[object]:
    return value if isinstance(value, list) else []


__all__ = [
    "ColumnMappingManifest",
    "ColumnMappingRecord",
    "DEFAULT_HARMONIZATION",
    "MANIFEST_FIELD_COLUMN_MAPPINGS",
    "MAPPING_FIELD_ALTERNATIVES",
    "MAPPING_FIELD_CDE_ID",
    "MAPPING_FIELD_CDE_KEY",
    "MAPPING_FIELD_COLUMN_NAME",
    "MAPPING_FIELD_CONFIDENCE",
    "MAPPING_FIELD_HARMONIZATION",
    "MAPPING_FIELD_ROUTE",
    "MAPPING_FIELD_TARGET",
    "MappingAlternative",
    "normalize_manifest",
]
