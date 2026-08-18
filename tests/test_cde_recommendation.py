from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest
from cde_recommend.types import (
    CDEMatch,
    ColumnError,
    ColumnResult,
    Harmonization,
    PotentialMatchIndex,
)

import src.integrations.cde_recommendation as recommendation_module
from src.domain.cde import CDEInfo, CdeType
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog
from src.domain.cde_recommendation import ProfiledColumn, RecommendationUnavailableError
from src.domain.column_profile import ColumnProfile, DistinctValue
from src.domain.columns import ColumnIdentity, column_key_from_string
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.reference_data import ReferenceModel
from src.integrations.cde_recommendation import CdeRecommendationAdapter


@dataclass
class _UnusedRanker:
    async def rank(self, developer_message: str, user_message: str) -> list:
        raise AssertionError("The controlled pipeline must replace model work")


@dataclass
class _UnusedCache:
    async def load_many(self, keys: list[str]) -> dict:
        raise AssertionError("The controlled pipeline must replace cache work")

    async def save_many(self, entries: list[tuple[str, ColumnResult]]) -> None:
        raise AssertionError("The controlled pipeline must replace cache work")


@dataclass
class _PipelineCapture:
    results: list[ColumnResult]
    calls: list[dict[str, object]] = field(default_factory=list)

    async def __call__(self, **kwargs: object) -> list[ColumnResult]:
        self.calls.append(kwargs)
        return self.results


@dataclass
class _FixedRanker:
    calls: int = 0

    async def rank(
        self,
        developer_message: str,
        user_message: str,
    ) -> list[PotentialMatchIndex]:
        self.calls += 1
        return [PotentialMatchIndex(candidate_index=0, rank=1, confidence=0.9)]


@dataclass
class _MemoryCache:
    entries: dict[str, ColumnResult] = field(default_factory=dict)

    async def load_many(self, keys: list[str]) -> dict[str, ColumnResult]:
        return {key: self.entries[key] for key in keys if key in self.entries}

    async def save_many(self, entries: list[tuple[str, ColumnResult]]) -> None:
        self.entries.update(entries)


def _profile(key: str, values: tuple[DistinctValue, ...]) -> ColumnProfile:
    return ColumnProfile(
        column_key=key,
        total_rows=sum(value.count for value in values),
        distinct_values=values,
        null_count=0,
    )


def _column(key: str, header: str, values: tuple[DistinctValue, ...]) -> ProfiledColumn:
    return ProfiledColumn(
        identity=ColumnIdentity(column_key_from_string(key), header),
        profile=_profile(key, values),
    )


def _reference_model() -> ReferenceModel:
    return ReferenceModel(
        version=DataModelVersionReference("CCDI", "2024.10"),
        label="CCDI",
        catalog=CdeCatalog.from_cdes([
            CDEInfo(42, "diagnosis", "Diagnosis", CdeType.PV),
            CDEInfo(None, "notes", "Notes", CdeType.PASSTHROUGH),
        ]),
        pvs=CdePvCatalog.from_mapping({
            "diagnosis": frozenset({"Lung", "Breast"}),
            "notes": frozenset(),
        }),
    )


def _result(header: str, cde_key: str = "diagnosis") -> ColumnResult:
    return ColumnResult(
        column_name=header,
        column_type="categorical",
        matches=[
            CDEMatch(
                cde_id=999,
                cde_key=cde_key,
                rank=1,
                confidence=0.91,
                harmonization=Harmonization.HARMONIZABLE,
            )
        ],
    )


async def _recommend(
    monkeypatch: pytest.MonkeyPatch,
    columns: Sequence[ProfiledColumn],
    results: list[ColumnResult],
) -> tuple[CdeRecommendationAdapter, _PipelineCapture]:
    capture = _PipelineCapture(results)
    monkeypatch.setattr(recommendation_module, "match_columns_batch", capture)
    adapter = CdeRecommendationAdapter(_UnusedRanker(), _UnusedCache(), concurrency=17)
    return adapter, capture


def test_profiled_column_rejects_mismatched_identity() -> None:
    # Given a profile and identity for different source columns.
    profile = _profile("col_0001", (DistinctValue("Lung", 1),))

    # When the recommendation input is constructed, then the mismatch stops.
    with pytest.raises(ValueError, match="does not match"):
        ProfiledColumn(
            identity=ColumnIdentity(column_key_from_string("col_0000"), "diagnosis"),
            profile=profile,
        )


@pytest.mark.asyncio
async def test_real_pipeline_reuses_identical_input_without_losing_column_identity() -> None:
    # Given two source columns have the same header and values but different stable keys.
    columns = [
        _column("col_0000", "source diagnosis", (DistinctValue("Lung", 2),)),
        _column("col_0001", "source diagnosis", (DistinctValue("Lung", 2),)),
    ]
    ranker = _FixedRanker()
    cache = _MemoryCache()
    adapter = CdeRecommendationAdapter(ranker, cache)

    # When a second recommendation request reuses the first request's cache entry.
    first = await adapter.recommend(columns, _reference_model())
    calls_after_first = ranker.calls
    second = await adapter.recommend(columns, _reference_model())

    # Then the real package pipeline skips new model work and both identities remain present.
    assert calls_after_first == 2
    assert ranker.calls == calls_after_first
    assert set(first.records) == set(second.records) == {
        column_key_from_string("col_0000"),
        column_key_from_string("col_0001"),
    }


@pytest.mark.asyncio
async def test_duplicate_headers_keep_distinct_column_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given two stable columns with the same display header.
    columns = [
        _column("col_0000", "diagnosis", (DistinctValue("Lung", 2),)),
        _column("col_0001", "diagnosis", (DistinctValue("Breast", 2),)),
    ]
    adapter, _capture = await _recommend(
        monkeypatch,
        columns,
        [_result("diagnosis"), _result("diagnosis")],
    )

    # When the package results return in input order.
    manifest = await adapter.recommend(columns, _reference_model())

    # Then both stable identities receive their own record.
    assert set(manifest.records) == {
        column_key_from_string("col_0000"),
        column_key_from_string("col_0001"),
    }
    assert [record.column_name for record in manifest.records.values()] == [
        "diagnosis",
        "diagnosis",
    ]


@pytest.mark.asyncio
async def test_adapter_bounds_profiles_and_passes_stable_catalog_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a billion-row profile and an unordered PV set.
    column = _column(
        "col_0000",
        "diagnosis",
        (
            DistinctValue("Lung", 900_000_000),
            DistinctValue("Breast", 100_000_000),
        ),
    )
    adapter, capture = await _recommend(monkeypatch, [column], [_result("diagnosis")])

    # When recommendation inputs are prepared.
    await adapter.recommend([column], _reference_model())

    # Then the profile is bounded and the catalog identity is provider-independent.
    request = capture.calls[0]
    package_columns = request["columns"]
    assert isinstance(package_columns, list)
    assert len(package_columns[0].column_values) == 5000
    assert package_columns[0].column_values.count("Lung") == 4500
    assert package_columns[0].column_values.count("Breast") == 500
    assert request["data_model_key"] == "CCDI"
    assert request["catalog_revision"] == "2024.10"
    assert request["concurrency"] == 17
    package_cdes = request["all_cdes"]
    assert isinstance(package_cdes, list)
    assert package_cdes[0].pv_values == ("Breast", "Lung")


@pytest.mark.asyncio
async def test_manifest_uses_reference_catalog_identity_not_provider_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the provider result carries an incorrect numeric CDE id.
    column = _column("col_0000", "diagnosis", (DistinctValue("Lung", 1),))
    adapter, _capture = await _recommend(monkeypatch, [column], [_result("diagnosis")])

    # When the result becomes a DataChord manifest.
    manifest = await adapter.recommend([column], _reference_model())

    # Then DataChord uses the trusted reference catalog id and preserves rank data.
    record = manifest.records[column.identity.key]
    assert record.cde_id == 42
    assert record.recommendation_source.value == "ai"
    assert record.alternatives[0].cde_id == 42
    assert record.alternatives[0].confidence == 0.91


@pytest.mark.asyncio
async def test_partial_failure_keeps_successful_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given one provider result succeeds and one fails.
    columns = [
        _column("col_0000", "diagnosis", (DistinctValue("Lung", 1),)),
        _column("col_0001", "notes", (DistinctValue("text", 1),)),
    ]
    failed = ColumnResult(
        column_name="notes",
        column_type="free_text",
        matches=[],
        error=ColumnError("matching_failed", "failed"),
    )
    adapter, _capture = await _recommend(monkeypatch, columns, [_result("diagnosis"), failed])

    # When the adapter builds the manifest.
    manifest = await adapter.recommend(columns, _reference_model())

    # Then the successful record remains and the failed record is omitted.
    assert list(manifest.records) == [column_key_from_string("col_0000")]


@pytest.mark.asyncio
async def test_total_provider_failure_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given every non-empty source column failed.
    column = _column("col_0000", "diagnosis", (DistinctValue("Lung", 1),))
    failed = ColumnResult(
        column_name="diagnosis",
        column_type="categorical",
        matches=[],
        error=ColumnError("matching_failed", "failed"),
    )
    adapter, _capture = await _recommend(monkeypatch, [column], [failed])

    # When recommendation is requested, then Stage 1 receives one typed outage.
    with pytest.raises(RecommendationUnavailableError):
        await adapter.recommend([column], _reference_model())


@pytest.mark.asyncio
async def test_unknown_provider_target_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the provider result names a CDE outside the selected reference model.
    column = _column("col_0000", "diagnosis", (DistinctValue("Lung", 1),))
    adapter, _capture = await _recommend(monkeypatch, [column], [_result("diagnosis", "invented")])

    # When conversion runs, then the untrusted target cannot enter the manifest.
    with pytest.raises(RecommendationUnavailableError, match="outside"):
        await adapter.recommend([column], _reference_model())
