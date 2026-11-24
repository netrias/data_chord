from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest

from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.harmonization import MatchFidelity
from src.domain.harmonization_cache import (
    HarmonizationCacheCorruptError,
    HarmonizationCacheEntry,
    HarmonizationCacheKey,
)
from src.integrations.dynamodb_harmonization_cache import DynamoDbHarmonizationCache


@dataclass
class _Table:
    items: dict[str, dict[str, object]] = field(default_factory=dict)

    def get_item(
        self, *, Key: Mapping[str, object], ConsistentRead: bool
    ) -> dict[str, object]:
        assert ConsistentRead is True
        item = self.items.get(str(Key["cache_key"]))
        return {"Item": item} if item is not None else {}

    def put_item(self, *, Item: Mapping[str, object]) -> dict[str, object]:
        self.items[str(Item["cache_key"])] = dict(Item)
        return {}


def _key(version: str = "11.0.4", cde_key: str = "diagnosis") -> HarmonizationCacheKey:
    return HarmonizationCacheKey(
        data_model_version=DataModelVersionReference("gc", version),
        cde_key=cde_key,
        source_value="raw source value",
    )


def test_cache_round_trips_one_exact_domain_identity() -> None:
    # Given an empty application cache and one complete harmonization result.
    table = _Table()
    cache = DynamoDbHarmonizationCache(table)
    entry = HarmonizationCacheEntry(
        key=_key(),
        matched_value="Approved value",
        match_fidelity=MatchFidelity.STRONG,
    )
    assert cache.load_many([entry.key]) == {}

    # When the result is saved and loaded again.
    cache.save_many([entry])
    loaded = cache.load_many([entry.key])

    # Then the exact domain result is restored from one non-enumerable hash key.
    assert loaded == {entry.key: entry}
    assert list(table.items.values()) == [
        {
            "cache_key": next(iter(table.items)),
            "schema_version": 1,
            "matched_value": "Approved value",
            "match_fidelity": "strong",
        }
    ]


def test_cache_separates_model_versions_and_cde_keys() -> None:
    # Given three results for the same raw source value under different domain scopes.
    table = _Table()
    cache = DynamoDbHarmonizationCache(table)
    entries = [
        HarmonizationCacheEntry(_key(), "Current", MatchFidelity.STRONG),
        HarmonizationCacheEntry(_key(version="12.0"), "New Version", MatchFidelity.PARTIAL),
        HarmonizationCacheEntry(_key(cde_key="treatment"), "Other CDE", MatchFidelity.APPROXIMATE),
    ]

    # When all identities are saved and loaded together.
    cache.save_many(entries)
    loaded = cache.load_many([entry.key for entry in entries])

    # Then every model-version and CDE boundary returns only its own result.
    assert loaded == {entry.key: entry for entry in entries}
    assert len(table.items) == 3


def test_cache_rejects_a_row_whose_identity_does_not_match_its_hash() -> None:
    # Given one stored row whose hash was changed after it was written.
    table = _Table()
    cache = DynamoDbHarmonizationCache(table)
    entry = HarmonizationCacheEntry(_key(), "Approved value", MatchFidelity.STRONG)
    cache.save_many([entry])
    next(iter(table.items.values()))["cache_key"] = "different"

    # When the original identity is loaded, then the corrupt row is rejected.
    with pytest.raises(HarmonizationCacheCorruptError, match="identity"):
        cache.load_many([entry.key])


def test_old_no_match_is_rechecked_without_discarding_existing_matches() -> None:
    # Given an old no-match row and a valid match from the same legacy writer.
    table = _Table()
    cache = DynamoDbHarmonizationCache(table)
    no_match = HarmonizationCacheEntry(_key(), None, MatchFidelity.NONE)
    match = HarmonizationCacheEntry(_key(cde_key="treatment"), "Approved", MatchFidelity.STRONG)
    cache.save_many([no_match, match])
    old_row = next(item for item in table.items.values() if item["matched_value"] is None)
    old_row["schema_version"] = 1
    assert all(row["schema_version"] == 1 for row in table.items.values())
    original_rows = {key: dict(row) for key, row in table.items.items()}

    # When the new reader looks up both outcomes.
    loaded = cache.load_many([no_match.key, match.key])

    # Then only the verified match is reused and the old no-match is untouched.
    assert loaded == {match.key: match}
    assert table.items == original_rows


def test_new_no_match_is_reusable() -> None:
    # Given a genuine no-match from the current explorer.
    table = _Table()
    cache = DynamoDbHarmonizationCache(table)
    entry = HarmonizationCacheEntry(_key(), None, MatchFidelity.NONE)
    cache.save_many([entry])
    assert [row["schema_version"] for row in table.items.values()] == [2]

    # When a later request asks for the same result.
    loaded = cache.load_many([entry.key])

    # Then the cache returns a verified no-match for that exact term.
    assert loaded == {entry.key: entry}
