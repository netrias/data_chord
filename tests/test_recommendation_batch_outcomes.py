import asyncio
import json
from dataclasses import dataclass, field

import pytest

from src.cde_recommend.recommendation_pipeline import match_columns_batch
from src.cde_recommend.result_cache import compute_cache_key
from src.cde_recommend.types import CDE, ColumnInput, ColumnResult, PotentialMatchIndex


@dataclass
class _RecordingCache:
    entries: dict[str, ColumnResult]
    reads: list[list[str]] = field(default_factory=list)
    writes: list[tuple[str, ColumnResult]] = field(default_factory=list)

    async def load_many(self, keys: list[str]) -> dict[str, ColumnResult]:
        self.reads.append(keys)
        return {key: self.entries[key] for key in keys if key in self.entries}

    async def save_many(self, entries: list[tuple[str, ColumnResult]]) -> None:
        self.writes.extend(entries)


@dataclass
class _MixedRanker:
    calls: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    no_match_finished: asyncio.Event = field(default_factory=asyncio.Event)

    async def rank(self, developer_message: str, user_message: str) -> list[PotentialMatchIndex]:
        source = json.loads(user_message.partition("\n")[2])
        name = source["column_name"]
        self.calls.append(name)
        if name == "broken":
            raise RuntimeError("controlled provider failure")
        if name == "unrelated":
            self.completed.append(name)
            self.no_match_finished.set()
            return []
        assert name == "clinical finding"
        await self.no_match_finished.wait()
        self.completed.append(name)
        return [PotentialMatchIndex(candidate_index=0, rank=1, confidence=0.9)]


@pytest.mark.asyncio
async def test_mixed_batch_preserves_order_and_caches_only_completed_model_results() -> None:
    # Given cached, exact, provider-match, provider-error, and no-match columns.
    columns = [
        ColumnInput("cached", ["Lung"]),
        ColumnInput("diagnosis", ["Lung"]),
        ColumnInput("clinical finding", ["Lung"]),
        ColumnInput("broken", ["1", "2"]),
        ColumnInput("unrelated", ["other"]),
    ]
    keys = [compute_cache_key("model", "revision", c.column_name, c.column_values, top_k=5) for c in columns]
    cached = ColumnResult("cached", [], "categorical")
    cache = _RecordingCache({keys[0]: cached})
    ranker = _MixedRanker()

    # When the real pipeline completes model calls in a different order from input.
    results = await asyncio.wait_for(
        match_columns_batch(
            columns,
            [CDE(42, "diagnosis", ("Lung",))],
            ranker=ranker,
            cache=cache,
            data_model_key="model",
            catalog_revision="revision",
            concurrency=3,
        ),
        timeout=5,
    )

    # Then all inputs keep their positions and failures cannot become cached no-matches.
    assert [result.column_name for result in results] == [column.column_name for column in columns]
    assert results[0] is cached
    assert [result.matches[0].cde_id for result in results[1:3]] == [42, 42]
    failed = results[3]
    assert failed.matches == []
    assert failed.column_type == "numeric"
    assert failed.error is not None and failed.error.code == "matching_failed"
    assert results[4].matches == [] and results[4].error is None
    assert set(ranker.calls) == {"clinical finding", "broken", "unrelated"}
    assert ranker.completed == ["unrelated", "clinical finding"]
    assert cache.reads == [keys]
    assert cache.writes == [(keys[2], results[2]), (keys[4], results[4])]
