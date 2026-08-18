"""DynamoDB boundary for reusable harmonization results."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Protocol, cast

from src.domain.harmonization import MatchFidelity
from src.domain.harmonization_cache import (
    HarmonizationCacheCorruptError,
    HarmonizationCacheEntry,
    HarmonizationCacheKey,
    HarmonizationCacheUnavailableError,
)

SCHEMA_VERSION = 1
_FIELDS = {
    "cache_key",
    "schema_version",
    "matched_value",
    "match_fidelity",
}


class DynamoTable(Protocol):
    def get_item(
        self, *, Key: Mapping[str, object], ConsistentRead: bool
    ) -> Mapping[str, object]: ...

    def put_item(self, *, Item: Mapping[str, object]) -> object: ...


class DynamoResource(Protocol):
    def Table(self, table_name: str) -> DynamoTable: ...  # noqa: N802 - boto3 framework name


class DynamoDbHarmonizationCache:
    """Point-lookup cache. Domain identity is stored only as a one-way digest."""

    def __init__(self, table: DynamoTable) -> None:
        self._table = table

    def load_many(
        self, keys: Sequence[HarmonizationCacheKey]
    ) -> Mapping[HarmonizationCacheKey, HarmonizationCacheEntry]:
        entries: dict[HarmonizationCacheKey, HarmonizationCacheEntry] = {}
        try:
            for key in dict.fromkeys(keys):
                response = self._table.get_item(
                    Key={"cache_key": _digest(key)},
                    ConsistentRead=True,
                )
                item = response.get("Item")
                if item is None:
                    continue
                if not isinstance(item, Mapping):
                    raise HarmonizationCacheCorruptError("Harmonization cache row is not an object")
                entries[key] = _entry_from_item(key, cast(Mapping[str, object], item))
        except HarmonizationCacheCorruptError:
            raise
        except Exception as exc:
            raise HarmonizationCacheUnavailableError("Harmonization cache read failed") from exc
        return entries

    def save_many(self, entries: Sequence[HarmonizationCacheEntry]) -> None:
        try:
            for entry in entries:
                self._table.put_item(Item=_item(entry))
        except Exception as exc:
            raise HarmonizationCacheUnavailableError("Harmonization cache write failed") from exc


def _item(entry: HarmonizationCacheEntry) -> Mapping[str, object]:
    key = entry.key
    return {
        "cache_key": _digest(key),
        "schema_version": SCHEMA_VERSION,
        "matched_value": entry.matched_value,
        "match_fidelity": entry.match_fidelity.value,
    }


def _entry_from_item(
    requested_key: HarmonizationCacheKey,
    item: Mapping[str, object],
) -> HarmonizationCacheEntry:
    if set(item) != _FIELDS or item.get("schema_version") != SCHEMA_VERSION:
        raise HarmonizationCacheCorruptError("Harmonization cache row has an unsupported shape")
    if item.get("cache_key") != _digest(requested_key):
        raise HarmonizationCacheCorruptError("Harmonization cache identity does not match its key")
    raw_match = item.get("matched_value")
    if raw_match is not None and not isinstance(raw_match, str):
        raise HarmonizationCacheCorruptError("Harmonization cache result is invalid")
    try:
        return HarmonizationCacheEntry(
            key=requested_key,
            matched_value=cast(str | None, raw_match),
            match_fidelity=MatchFidelity(_required_string(item, "match_fidelity")),
        )
    except ValueError as exc:
        raise HarmonizationCacheCorruptError("Harmonization cache result is invalid") from exc


def _required_string(item: Mapping[str, object], field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value:
        raise HarmonizationCacheCorruptError(f"Harmonization cache field is invalid: {field}")
    return value


def _digest(key: HarmonizationCacheKey) -> str:
    payload = {
        "data_model_key": key.data_model_version.data_model_key,
        "external_version_number": key.data_model_version.external_version_number,
        "cde_key": key.cde_key,
        "source_value": key.source_value,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "DynamoDbHarmonizationCache",
    "DynamoResource",
    "DynamoTable",
    "SCHEMA_VERSION",
]
