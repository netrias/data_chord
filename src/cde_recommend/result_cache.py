"""DynamoDB-backed cache of ColumnResult keyed by matching-input hash.

The persisted row shape is a derived view over ColumnResult; ColumnResult
itself remains the canonical in-memory type in types.py. Changes when the
cache key, row layout, or eviction policy changes.
"""

import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Protocol, get_args

import boto3

from src.cde_recommend.types import CDEMatch, ColumnResult, ColumnType, Harmonization

# Derived from the ColumnType Literal so types.py remains the single owner
# of the dtype set — adding a dtype there automatically updates the validator.
_VALID_COLUMN_TYPES: tuple[str, ...] = get_args(ColumnType)
# Derived from the Harmonization StrEnum for the same reason — adding a variant
# there automatically updates the cache-row validator without a second edit.
_VALID_HARMONIZATIONS: frozenset[str] = frozenset(h.value for h in Harmonization)

logger = logging.getLogger(__name__)

# 30 days balances replay cost (re-running an identical request hits the cache
# for a month) against staleness risk (CDE catalog updates eventually propagate
# on their own as version_id changes, which invalidates old keys by design).
_TTL_DAYS = 30
_SECONDS_PER_DAY = 86400
# DynamoDB hard limits on batched operations. Splitting into multiple calls
# below lets us cache an arbitrary number of columns per request without
# exceeding the SDK's per-call bounds.
_BATCH_READ_LIMIT = 100
_BATCH_WRITE_LIMIT = 25
_BATCH_ATTEMPTS = 4
_RETRY_BASE_SECONDS = 0.05

# Bumped because the cache now accepts the caller's stable catalog revision
# instead of an internal database identifier.
_CACHE_KEY_PREFIX = "v7:"


class RecommendationCache(Protocol):
    """Result-cache behavior required by the recommendation pipeline."""

    async def load_many(self, keys: list[str]) -> dict[str, ColumnResult]: ...

    async def save_many(self, entries: list[tuple[str, ColumnResult]]) -> None: ...


class DynamoRecommendationCache:
    """Run the synchronous boto3 cache adapter outside the async event loop."""

    def __init__(self, table_name: str, region: str) -> None:
        if not table_name.strip():
            raise ValueError("Recommendation cache table name is required")
        if not region.strip():
            raise ValueError("Recommendation cache region is required")
        self._table_name = table_name
        self._region = region

    async def load_many(self, keys: list[str]) -> dict[str, ColumnResult]:
        return await asyncio.to_thread(
            _load_cached_results,
            keys,
            table_name=self._table_name,
            region=self._region,
        )

    async def save_many(self, entries: list[tuple[str, ColumnResult]]) -> None:
        await asyncio.to_thread(
            _store_results,
            entries,
            table_name=self._table_name,
            region=self._region,
        )


def compute_cache_key(
    data_model_key: str,
    catalog_revision: str,
    column_name: str,
    column_values: list[str],
    *,
    top_k: int,
) -> str:
    """Deterministic SHA-256 hash of all inputs that affect matching output.

    Keyed on the caller's stable catalog revision so each published catalog
    snapshot has its own cache namespace.
    """
    # Preserve value order because profiling uses a bounded prefix. Two request
    # orders can produce different prompts and must not share a cache result.
    # sort_keys=True only normalizes the top-level dict-key ordering.
    payload = json.dumps(
        {
            "data_model_key": data_model_key,
            "catalog_revision": catalog_revision,
            "column_name": column_name,
            "column_values": column_values,
            "top_k": top_k,
        },
        sort_keys=True,
    )
    # SHA-256 over the JSON payload: cryptographic strength isn't required,
    # but it gives collision-free keys and is fast enough to compute per
    # request without being a hot spot.
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return f"{_CACHE_KEY_PREFIX}{digest}"


def _load_cached_results(
    keys: list[str],
    *,
    table_name: str,
    region: str,
) -> dict[str, ColumnResult]:
    """Look up a batch of cache keys; return only hits (misses are absent).

    Callers align the returned dict back to their input list positionally;
    absence of a key means "miss" and triggers the LLM path. A partial
    DynamoDB failure degrades gracefully — we log and continue, treating the
    failed batch as all misses.
    """
    dynamodb = _get_resource(region)
    results: dict[str, ColumnResult] = {}
    # DynamoDB rejects duplicate primary keys in one BatchGetItem request.
    # The caller still keeps its positional key list and can reuse this one
    # returned value at every matching position.
    unique_keys = list(dict.fromkeys(keys))

    # Split into batches at the DynamoDB hard limit. Separate batches are
    # independent — one failure does not abort the others.
    for i in range(0, len(unique_keys), _BATCH_READ_LIMIT):
        batch_keys = unique_keys[i : i + _BATCH_READ_LIMIT]
        for item in _load_batch(dynamodb, table_name, batch_keys):
            cache_key = item["cache_key"]
            # Any row we cannot decode — malformed JSON, missing required field,
            # wrong type, or a legacy-prefix entry that slipped through on a
            # shared table — is treated as a miss rather than crashing the request.
            try:
                result_data = json.loads(item["result"])
                results[cache_key] = _deserialize_column_result(result_data)
            except (ValueError, KeyError, TypeError):
                logger.warning("Skipping unreadable cache entry %s", cache_key)

    return results


def _store_results(
    entries: list[tuple[str, ColumnResult]],
    *,
    table_name: str,
    region: str,
) -> None:
    """Persist fresh results to the cache. Best-effort; failures are logged.

    Callers feed only successful matches here — we intentionally do not cache
    transient failures so the next invocation can retry.
    """
    dynamodb = _get_resource(region)
    # Compute TTL once per call so every row in this batch expires at the
    # same wall-clock moment — avoids artificial staggering from per-row
    # time.time() jitter.
    ttl = int(time.time()) + (_TTL_DAYS * _SECONDS_PER_DAY)
    # BatchWriteItem also rejects duplicate primary keys. Keeping the last
    # value matches the result of sequential writes for the same cache key.
    unique_entries = list(dict(entries).items())

    for i in range(0, len(unique_entries), _BATCH_WRITE_LIMIT):
        batch = unique_entries[i : i + _BATCH_WRITE_LIMIT]
        requests = [
            {
                "PutRequest": {
                    "Item": {
                        "cache_key": cache_key,
                        "result": json.dumps(_serialize_column_result(result)),
                        "created_at": int(time.time()),
                        "ttl": ttl,
                    }
                }
            }
            for cache_key, result in batch
        ]
        _store_batch(dynamodb, table_name, requests)


def _load_batch(
    dynamodb: Any,
    table_name: str,
    keys: list[str],
) -> list[dict[str, Any]]:
    pending = [{"cache_key": key} for key in keys]
    items: list[dict[str, Any]] = []
    for attempt in range(_BATCH_ATTEMPTS):
        try:
            response = dynamodb.batch_get_item(
                RequestItems={table_name: {"Keys": pending}}
            )
        except Exception:
            logger.exception("DynamoDB batch_get_item failed")
            return items
        items.extend(response.get("Responses", {}).get(table_name, []))
        pending = (
            response.get("UnprocessedKeys", {})
            .get(table_name, {})
            .get("Keys", [])
        )
        if not pending:
            return items
        _wait_before_retry(attempt)
    logger.warning("DynamoDB left %d recommendation cache keys unprocessed", len(pending))
    return items


def _store_batch(
    dynamodb: Any,
    table_name: str,
    requests: list[dict[str, Any]],
) -> None:
    pending = requests
    for attempt in range(_BATCH_ATTEMPTS):
        try:
            response = dynamodb.batch_write_item(
                RequestItems={table_name: pending}
            )
        except Exception:
            logger.exception("DynamoDB batch_write_item failed")
            return
        pending = response.get("UnprocessedItems", {}).get(table_name, [])
        if not pending:
            return
        _wait_before_retry(attempt)
    logger.warning("DynamoDB left %d recommendation cache writes unprocessed", len(pending))


def _wait_before_retry(attempt: int) -> None:
    if attempt + 1 < _BATCH_ATTEMPTS:
        time.sleep(_RETRY_BASE_SECONDS * (2**attempt))


def _get_resource(region: str) -> Any:
    """boto3 service resources are dynamically typed.

    Returning ``Any`` (not ``object``) avoids forcing every call site to add
    per-line type-ignore suppressions on ``.batch_get_item`` / ``.batch_write_item``.
    """
    return boto3.resource(
        "dynamodb",
        region_name=region,
    )


def _serialize_column_result(result: ColumnResult) -> dict:
    return {
        "column_name": result.column_name,
        "column_type": result.column_type,
        "matches": [
            {
                "cde_id": m.cde_id,
                "cde_key": m.cde_key,
                "rank": m.rank,
                "confidence": m.confidence,
                "harmonization": m.harmonization.value,
            }
            for m in result.matches
        ],
    }


def _deserialize_column_result(data: dict) -> ColumnResult:
    """Deserialize a v4 cache row into a ColumnResult; raise if structurally invalid.

    Raises ``ValueError`` / ``KeyError`` / ``TypeError`` for any missing or
    malformed field. Callers in ``_load_cached_results`` catch all three and
    treat the entry as a cache miss. The row-validity policy is uniform:
    every field uses strict access so a structurally broken row never
    surfaces as a partially-populated match (e.g. a zero-confidence hit).
    The harmonization check is belt-and-suspenders — the v3 → v4 prefix bump
    structurally orphans every pre-harmonization row, so this path never sees
    one in practice, but a future drift will fail loudly rather than silently
    defaulting to a stale enum value.
    """
    raw_column_type = data.get("column_type")
    if raw_column_type not in _VALID_COLUMN_TYPES:
        raise ValueError(
            f"Cached entry missing or invalid column_type: {raw_column_type!r}; "
            f"expected one of {_VALID_COLUMN_TYPES}."
        )
    column_type: ColumnType = raw_column_type  # type: ignore[assignment]

    matches: list[CDEMatch] = []
    for m in data["matches"]:
        raw_harmonization = m.get("harmonization")
        if raw_harmonization not in _VALID_HARMONIZATIONS:
            raise ValueError(
                f"Cached match missing or invalid harmonization: {raw_harmonization!r}; "
                f"expected one of {sorted(_VALID_HARMONIZATIONS)}."
            )
        matches.append(
            CDEMatch(
                cde_id=m["cde_id"],
                cde_key=m["cde_key"],
                rank=m["rank"],
                confidence=m["confidence"],
                harmonization=Harmonization(raw_harmonization),
            )
        )
    return ColumnResult(column_name=data["column_name"], matches=matches, column_type=column_type)
