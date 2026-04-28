"""Column-keyed CDE mapping manifests at the app and SDK boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from src.domain.cde import ModelSuggestion
from src.domain.column_cde_map import ColumnCdeMap
from src.domain.columns import ColumnKey, column_key_from_string
from src.domain.manifest.models import AlternativeEntry, ColumnMappingEntry, ManifestPayload


@dataclass(frozen=True)
class MappingAlternative:
    target: str
    confidence: float
    cde_id: int | None = None
    harmonization: str | None = None

    @classmethod
    def from_payload(cls, payload: object) -> MappingAlternative | None:
        if not isinstance(payload, Mapping):
            return None
        target = payload.get("target")
        if not isinstance(target, str) or not target:
            return None
        return cls(
            target=target,
            confidence=_score_from_payload(payload),
            cde_id=_int_or_none(payload.get("cde_id")),
            harmonization=_str_or_none(payload.get("harmonization")),
        )

    def to_payload(self) -> AlternativeEntry:
        payload = AlternativeEntry(target=self.target, confidence=self.confidence)
        if self.cde_id is not None:
            payload["cde_id"] = self.cde_id
        if self.harmonization is not None:
            payload["harmonization"] = self.harmonization
        return payload

    def to_suggestion(self) -> ModelSuggestion:
        return ModelSuggestion(target=self.target, similarity=self.confidence)


@dataclass(frozen=True)
class ColumnMappingRecord:
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
        cde_id = _int_or_none(payload.get("cde_id"))
        if cde_key is None or cde_id is None:
            return None
        alternatives = tuple(
            alternative
            for raw in _list_or_empty(payload.get("alternatives"))
            if (alternative := MappingAlternative.from_payload(raw)) is not None
        )
        return cls(
            column_key=column_key_from_string(column_key),
            cde_key=cde_key,
            cde_id=cde_id,
            column_name=_str_or_none(payload.get("column_name")),
            harmonization=_str_or_none(payload.get("harmonization")),
            route=_str_or_none(payload.get("route")),
            alternatives=alternatives,
        )

    def to_payload(self) -> ColumnMappingEntry:
        payload = ColumnMappingEntry(cde_key=self.cde_key, cde_id=self.cde_id)
        if self.column_name is not None:
            payload["column_name"] = self.column_name
        if self.harmonization is not None:
            payload["harmonization"] = self.harmonization
        if self.route is not None:
            payload["route"] = self.route
        if self.alternatives:
            payload["alternatives"] = [alternative.to_payload() for alternative in self.alternatives]
        return payload

    def suggestions(self) -> list[ModelSuggestion]:
        if self.alternatives:
            return [alternative.to_suggestion() for alternative in self.alternatives]
        return [ModelSuggestion(target=self.cde_key, similarity=1.0)]


@dataclass(frozen=True)
class ColumnMappingManifest:
    records: dict[ColumnKey, ColumnMappingRecord]

    @classmethod
    def empty(cls) -> ColumnMappingManifest:
        return cls(records={})

    @classmethod
    def from_payload(cls, payload: Mapping[str, object] | object | None) -> ColumnMappingManifest:
        if not isinstance(payload, Mapping):
            return cls.empty()
        raw_mappings = payload.get("column_mappings")
        if not isinstance(raw_mappings, Mapping):
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
            "column_mappings": {
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

    def column_cde_map(self) -> ColumnCdeMap:
        return ColumnCdeMap({column_key: record.cde_key for column_key, record in self.records.items()})

    def suggestions_by_column(self) -> dict[str, list[ModelSuggestion]]:
        return {str(column_key): record.suggestions() for column_key, record in self.records.items()}


def normalize_manifest(payload: Mapping[str, object] | object | None) -> ManifestPayload:
    return ColumnMappingManifest.from_payload(payload).to_payload()


def _cde_key_from_payload(payload: Mapping[object, object]) -> str | None:
    cde_key = payload.get("cde_key")
    if isinstance(cde_key, str) and cde_key:
        return cde_key
    return None


def _score_from_payload(payload: Mapping[object, object]) -> float:
    confidence = payload.get("confidence")
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
    "MappingAlternative",
    "normalize_manifest",
]
