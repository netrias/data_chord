"""Agentic harmonization adapter behavior."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace

from netrias_client import read_tabular

from src.domain.columns import column_key_for_index
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.harmonization import HarmonizeStatus, MatchFidelity
from src.domain.harmonization_cache import (
    HarmonizationCache,
    HarmonizationCacheEntry,
    HarmonizationCacheKey,
    HarmonizationCacheUnavailableError,
)
from src.domain.manifest import ColumnMappingManifest
from src.integrations.agentic_harmonize import (
    AgenticHarmonizeConfig,
    AgenticTermHarmonizer,
)
from src.integrations.harmonize import (
    FileHarmonizationService,
    TermHarmonizationRequest,
    TermHarmonizationResponse,
)
from src.persistence.manifest_reader import read_manifest_parquet
from src.persistence.pv_manifest_store import ColumnPvSets

MODEL_VERSION = DataModelVersionReference("gc", "11.0.4")


class _MemoryCache:
    def __init__(self) -> None:
        self.entries: dict[HarmonizationCacheKey, HarmonizationCacheEntry] = {}
        self.loaded_keys: list[HarmonizationCacheKey] = []
        self.save_calls = 0

    def load_many(
        self, keys: Sequence[HarmonizationCacheKey]
    ) -> Mapping[HarmonizationCacheKey, HarmonizationCacheEntry]:
        self.loaded_keys.extend(keys)
        return {key: self.entries[key] for key in keys if key in self.entries}

    def save_many(self, entries: Sequence[HarmonizationCacheEntry]) -> None:
        self.save_calls += 1
        self.entries.update({entry.key: entry for entry in entries})


class _UnavailableCache:
    def load_many(
        self, keys: Sequence[HarmonizationCacheKey]
    ) -> Mapping[HarmonizationCacheKey, HarmonizationCacheEntry]:
        raise HarmonizationCacheUnavailableError("read unavailable")

    def save_many(self, entries: Sequence[HarmonizationCacheEntry]) -> None:
        raise HarmonizationCacheUnavailableError("write unavailable")


def _manifest() -> ColumnMappingManifest:
    return ColumnMappingManifest.from_payload_strict(
        {
            "column_mappings": {
                "col_0000": {"cde_key": "diagnosis", "cde_id": 1},
                "col_0001": {"cde_key": "treatment", "cde_id": 2},
            }
        }
    )


def _prediction(term: str, match: str, fidelity: str = "strong") -> SimpleNamespace:
    return SimpleNamespace(
        prediction=SimpleNamespace(
            input_term=term,
            predicted_match=match,
            match_fidelity=fidelity,
        )
    )


class _DeterministicProvider:
    def __init__(self, match_index: int = 0) -> None:
        self.match_index = match_index
        self.requests: list[TermHarmonizationRequest] = []

    def harmonize(
        self,
        requests: tuple[TermHarmonizationRequest, ...],
    ) -> tuple[TermHarmonizationResponse, ...]:
        self.requests.extend(requests)
        return tuple(
            TermHarmonizationResponse(
                matched_value=request.permissible_values[self.match_index],
                match_fidelity=MatchFidelity.STRONG,
            )
            for request in requests
        )


def _agentic_file_service(
    config: AgenticHarmonizeConfig,
    *,
    cache: HarmonizationCache | None = None,
) -> FileHarmonizationService:
    return FileHarmonizationService(AgenticTermHarmonizer(config), cache=cache)


def test_agentic_defaults_use_bedrock_gpt_56_with_high_reasoning() -> None:
    # Given no agentic model overrides.

    # When the default configuration is created.
    config = AgenticHarmonizeConfig(region="us-east-2")

    # Then every agent role uses the approved model and reasoning settings.
    assert config.explorer_model.name == "gpt-5.6-luna"
    assert config.shortlister_model.name == "gpt-5.6-luna"
    assert config.selector_model.name == "gpt-5.6-sol"
    assert config.reasoning_effort == "high"
    assert config.exploration_turns == 10
    assert config.max_workers == 100


def test_agentic_harmonization_collapses_duplicates_only_within_each_column(
    monkeypatch,
    tmp_path: Path,
) -> None:
    # Given duplicate values occur in two columns with different CDEs.
    source = tmp_path / "source.csv"
    source.write_text("diagnosis,treatment\nsame,same\nsame,same\n", encoding="utf-8")
    output = tmp_path / "output.csv"
    calls: list[tuple[str, str]] = []
    model_calls: list[tuple[str, str, str]] = []
    client_calls: list[tuple[str, object, object]] = []

    def fake_harmonize(_client, _index, term: str, **kwargs: object) -> SimpleNamespace:
        context = str(kwargs["context"])
        calls.append((term, context))
        model_calls.append(
            (
                kwargs["explorer_model"].name,  # type: ignore[union-attr]
                kwargs["shortlister_model"].name,  # type: ignore[union-attr]
                kwargs["selector_model"].name,  # type: ignore[union-attr]
            )
        )
        match = "Diagnosis Match" if "diagnosis" in context else "Treatment Match"
        return _prediction(term, match)

    monkeypatch.setattr("src.integrations.agentic_harmonize.harmonize_term", fake_harmonize)

    def fake_client(region: str, *, provider: object, reasoning_effort: object) -> object:
        client_calls.append((region, provider, reasoning_effort))
        return object()

    monkeypatch.setattr("src.integrations.agentic_harmonize.make_provider_client", fake_client)
    service = _agentic_file_service(AgenticHarmonizeConfig(region="us-east-2", max_workers=2))

    # When the file service runs with the agentic term provider.
    result = service.run(
        file_path=source,
        data_model_version=MODEL_VERSION,
        prepared_manifest=_manifest(),
        column_pv_sets=ColumnPvSets(
            {
                column_key_for_index(0): frozenset({"Diagnosis Match"}),
                column_key_for_index(1): frozenset({"Treatment Match"}),
            }
        ),
        output_path=output,
    )

    # Then one provider request is made per distinct term and CDE pair.
    assert result.status is HarmonizeStatus.SUCCEEDED
    assert len(calls) == 2
    assert set(model_calls) == {("gpt-5.6-luna", "gpt-5.6-luna", "gpt-5.6-sol")}
    assert client_calls
    assert all(
        region == "us-east-2" and str(provider) == "aws-bedrock" and str(effort) == "high"
        for region, provider, effort in client_calls
    )
    assert {context for _term, context in calls} == {
        "Source column: diagnosis\nTarget CDE: diagnosis",
        "Source column: treatment\nTarget CDE: treatment",
    }
    assert read_tabular(output).rows == [
        ["Diagnosis Match", "Treatment Match"],
        ["Diagnosis Match", "Treatment Match"],
    ]
    summary = read_manifest_parquet(result.manifest_path) if result.manifest_path else None
    assert summary is not None
    assert [(row.column_id, row.to_harmonize, row.row_indices) for row in summary.rows] == [
        (0, "same", [0, 1]),
        (1, "same", [0, 1]),
    ]
    assert {row.match_fidelity for row in summary.rows} == {MatchFidelity.STRONG}


def test_agentic_harmonization_passes_through_columns_without_pvs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    # Given both mapped columns have no permissible values.
    source = tmp_path / "source.csv"
    source.write_text("diagnosis,treatment\nunknown,keep\n", encoding="utf-8")
    output = tmp_path / "output.csv"
    monkeypatch.setattr(
        "src.integrations.agentic_harmonize.harmonize_term",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("provider must not be called")),
    )
    service = _agentic_file_service(AgenticHarmonizeConfig(region="us-east-2"))

    # When the file service runs.
    result = service.run(
        file_path=source,
        data_model_version=MODEL_VERSION,
        prepared_manifest=_manifest(),
        column_pv_sets=ColumnPvSets(
            {
                column_key_for_index(0): frozenset(),
                column_key_for_index(1): None,
            }
        ),
        output_path=output,
    )

    # Then source values pass through without a provider request.
    assert result.status is HarmonizeStatus.SUCCEEDED
    assert read_tabular(output).rows == [["unknown", "keep"]]
    summary = read_manifest_parquet(result.manifest_path) if result.manifest_path else None
    assert summary is not None
    assert all(row.match_fidelity is MatchFidelity.NONE for row in summary.rows)


def test_agentic_no_match_keeps_the_source_value(monkeypatch, tmp_path: Path) -> None:
    # Given the agentic provider returns NO_MATCH for each source value.
    source = tmp_path / "source.csv"
    source.write_text("diagnosis,treatment\nunknown,keep\n", encoding="utf-8")
    output = tmp_path / "output.csv"
    monkeypatch.setattr(
        "src.integrations.agentic_harmonize.harmonize_term",
        lambda _client, _index, term, **_kwargs: _prediction(term, "NO_MATCH", "none"),
    )
    monkeypatch.setattr("src.integrations.agentic_harmonize.make_provider_client", lambda *args, **kwargs: object())
    service = _agentic_file_service(AgenticHarmonizeConfig(region="us-east-2", max_workers=2))

    # When the file service runs.
    result = service.run(
        file_path=source,
        data_model_version=MODEL_VERSION,
        prepared_manifest=_manifest(),
        column_pv_sets=ColumnPvSets(
            {
                column_key_for_index(0): frozenset({"Known"}),
                column_key_for_index(1): frozenset({"Kept"}),
            }
        ),
        output_path=output,
    )

    # Then each original source value remains unchanged.
    assert result.status is HarmonizeStatus.SUCCEEDED
    assert read_tabular(output).rows == [["unknown", "keep"]]
    summary = read_manifest_parquet(result.manifest_path) if result.manifest_path else None
    assert summary is not None
    assert all(row.match_fidelity is MatchFidelity.NONE for row in summary.rows)


def test_agentic_harmonization_fails_without_output_when_one_term_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    # Given one agentic term request fails.
    source = tmp_path / "source.csv"
    source.write_text("diagnosis,treatment\ngood,bad\n", encoding="utf-8")
    output = tmp_path / "output.csv"

    def fake_harmonize(_client, _index, term: str, **_kwargs: object) -> SimpleNamespace:
        if term == "bad":
            raise RuntimeError("provider secret")
        return _prediction(term, "Diagnosis Match")

    monkeypatch.setattr("src.integrations.agentic_harmonize.harmonize_term", fake_harmonize)
    monkeypatch.setattr("src.integrations.agentic_harmonize.make_provider_client", lambda *args, **kwargs: object())
    service = _agentic_file_service(AgenticHarmonizeConfig(region="us-east-2", max_workers=2))

    # When the file service runs the complete input.
    result = service.run(
        file_path=source,
        data_model_version=MODEL_VERSION,
        prepared_manifest=_manifest(),
        column_pv_sets=ColumnPvSets(
            {
                column_key_for_index(0): frozenset({"Diagnosis Match"}),
                column_key_for_index(1): frozenset({"Treatment Match"}),
            }
        ),
        output_path=output,
    )

    # Then it publishes neither a partial output nor a partial manifest.
    assert result.status is HarmonizeStatus.FAILED
    assert result.detail == "Harmonization provider failed."
    assert result.manifest_path is None
    assert result.output_path is None
    assert not output.exists()
    assert not output.with_name("output.manifest.parquet").exists()


def test_exact_permissible_value_skips_cache_and_bedrock(
    monkeypatch,
    tmp_path: Path,
) -> None:
    # Given one exact permissible value and one value that needs harmonization.
    source = tmp_path / "source.csv"
    source.write_text("diagnosis\nKnown\nunknown\n", encoding="utf-8")
    output = tmp_path / "output.csv"
    cache = _MemoryCache()
    calls: list[str] = []

    def fake_harmonize(_client, _index, term: str, **_kwargs: object) -> SimpleNamespace:
        calls.append(term)
        return _prediction(term, "Matched")

    monkeypatch.setattr("src.integrations.agentic_harmonize.harmonize_term", fake_harmonize)
    monkeypatch.setattr(
        "src.integrations.agentic_harmonize.make_provider_client",
        lambda *args, **kwargs: object(),
    )
    service = _agentic_file_service(
        AgenticHarmonizeConfig(region="us-east-2"),
        cache=cache,
    )

    # When the dataset is harmonized.
    result = service.run(
        file_path=source,
        data_model_version=MODEL_VERSION,
        prepared_manifest=_manifest(),
        column_pv_sets=ColumnPvSets(
            {
                column_key_for_index(0): frozenset({"Known", "Matched"}),
            }
        ),
        output_path=output,
    )

    # Then only the non-matching value reaches Bedrock or the cache.
    assert result.status is HarmonizeStatus.SUCCEEDED
    assert calls == ["unknown"]
    assert read_tabular(output).rows == [["Known"], ["Matched"]]
    assert {key.source_value for key in cache.loaded_keys} == {"unknown"}
    assert {entry.key.source_value for entry in cache.entries.values()} == {"unknown"}


def test_second_run_uses_the_versioned_cde_cache_without_bedrock(
    monkeypatch,
    tmp_path: Path,
) -> None:
    # Given an empty cache and one source value that needs Bedrock.
    source = tmp_path / "source.csv"
    source.write_text("diagnosis\nunknown\n", encoding="utf-8")
    first_output = tmp_path / "first.csv"
    second_output = tmp_path / "second.csv"
    cache = _MemoryCache()
    calls: list[str] = []

    def fake_harmonize(_client, _index, term: str, **_kwargs: object) -> SimpleNamespace:
        calls.append(term)
        return _prediction(term, "Matched")

    monkeypatch.setattr("src.integrations.agentic_harmonize.harmonize_term", fake_harmonize)
    monkeypatch.setattr(
        "src.integrations.agentic_harmonize.make_provider_client",
        lambda *args, **kwargs: object(),
    )
    service = _agentic_file_service(
        AgenticHarmonizeConfig(region="us-east-2"),
        cache=cache,
    )
    arguments = {
        "file_path": source,
        "data_model_version": MODEL_VERSION,
        "prepared_manifest": _manifest(),
        "column_pv_sets": ColumnPvSets(
            {
                column_key_for_index(0): frozenset({"Matched"}),
            }
        ),
    }

    # When the same model, CDE, and raw source value are harmonized twice.
    first = service.run(**arguments, output_path=first_output)
    second = service.run(**arguments, output_path=second_output)

    # Then the first run fills the cache and the second run makes no Bedrock call.
    assert first.status is HarmonizeStatus.SUCCEEDED
    assert second.status is HarmonizeStatus.SUCCEEDED
    assert calls == ["unknown"]
    assert read_tabular(second_output).rows == [["Matched"]]


def test_stale_cached_match_is_replaced_by_current_provider_result(tmp_path: Path) -> None:
    # Given a cache entry whose match is not in the current permissible values.
    source = tmp_path / "source.csv"
    source.write_text("diagnosis\nunknown\n", encoding="utf-8")
    output = tmp_path / "output.csv"
    key = HarmonizationCacheKey(
        data_model_version=MODEL_VERSION,
        cde_key="diagnosis",
        source_value="unknown",
    )
    cache = _MemoryCache()
    cache.entries[key] = HarmonizationCacheEntry(
        key=key,
        matched_value="Old Match",
        match_fidelity=MatchFidelity.STRONG,
    )
    provider = _DeterministicProvider()
    service = FileHarmonizationService(provider, cache=cache)

    # When the dataset runs against the current permissible-value set.
    result = service.run(
        file_path=source,
        data_model_version=MODEL_VERSION,
        prepared_manifest=_manifest(),
        column_pv_sets=ColumnPvSets({
            column_key_for_index(0): frozenset({"New Match"}),
        }),
        output_path=output,
    )

    # Then the stale cache entry is ignored and replaced with the valid provider result.
    assert result.status is HarmonizeStatus.SUCCEEDED
    assert read_tabular(output).rows == [["New Match"]]
    assert [request.input_term for request in provider.requests] == ["unknown"]
    assert cache.entries[key].matched_value == "New Match"


def test_unavailable_cache_does_not_block_bedrock_or_the_result(
    monkeypatch,
    tmp_path: Path,
) -> None:
    # Given the cache cannot read or write, but Bedrock can harmonize the value.
    source = tmp_path / "source.csv"
    source.write_text("diagnosis\nunknown\n", encoding="utf-8")
    output = tmp_path / "output.csv"
    calls: list[str] = []

    def fake_harmonize(_client, _index, term: str, **_kwargs: object) -> SimpleNamespace:
        calls.append(term)
        return _prediction(term, "Matched")

    monkeypatch.setattr("src.integrations.agentic_harmonize.harmonize_term", fake_harmonize)
    monkeypatch.setattr(
        "src.integrations.agentic_harmonize.make_provider_client",
        lambda *args, **kwargs: object(),
    )
    service = _agentic_file_service(
        AgenticHarmonizeConfig(region="us-east-2"),
        cache=_UnavailableCache(),
    )

    # When the dataset is harmonized.
    result = service.run(
        file_path=source,
        data_model_version=MODEL_VERSION,
        prepared_manifest=_manifest(),
        column_pv_sets=ColumnPvSets(
            {
                column_key_for_index(0): frozenset({"Matched"}),
            }
        ),
        output_path=output,
    )

    # Then Bedrock produces the result and cache failure does not fail the job.
    assert result.status is HarmonizeStatus.SUCCEEDED
    assert calls == ["unknown"]
    assert read_tabular(output).rows == [["Matched"]]


def test_service_uses_injected_provider_for_deterministic_output(tmp_path: Path) -> None:
    # Given a deterministic term provider and one non-exact source term.
    source = tmp_path / "source.csv"
    source.write_text("diagnosis\nunknown\n", encoding="utf-8")
    output = tmp_path / "output.csv"
    provider = _DeterministicProvider()
    service = FileHarmonizationService(provider)

    # When the real harmonization service runs the dataset.
    result = service.run(
        file_path=source,
        data_model_version=MODEL_VERSION,
        prepared_manifest=_manifest(),
        column_pv_sets=ColumnPvSets({
            column_key_for_index(0): frozenset({"Known", "Matched"}),
        }),
        output_path=output,
    )

    # Then the service writes the injected provider's result without a live provider call.
    assert result.status is HarmonizeStatus.SUCCEEDED
    assert read_tabular(output).rows == [["Known"]]
    assert provider.requests == [
        TermHarmonizationRequest(
            cde="diagnosis",
            input_term="unknown",
            permissible_values=("Known", "Matched"),
            context="Source column: diagnosis\nTarget CDE: diagnosis",
        )
    ]


def test_use_cache_false_skips_cache_read_and_write(tmp_path: Path) -> None:
    # Given a cache populated by a first run and a provider with a changed result.
    source = tmp_path / "source.csv"
    source.write_text("diagnosis\nunknown\n", encoding="utf-8")
    first_output = tmp_path / "first.csv"
    second_output = tmp_path / "second.csv"
    cache = _MemoryCache()
    provider = _DeterministicProvider(match_index=0)
    service = FileHarmonizationService(provider, cache=cache)
    arguments = {
        "file_path": source,
        "data_model_version": MODEL_VERSION,
        "prepared_manifest": _manifest(),
        "column_pv_sets": ColumnPvSets({
            column_key_for_index(0): frozenset({"Known", "Matched"}),
        }),
    }
    first = service.run(**arguments, output_path=first_output)
    provider.match_index = 1
    loaded_keys_before_second_run = list(cache.loaded_keys)

    # When the same work runs with cache use disabled.
    second = service.run(**arguments, output_path=second_output, use_cache=False)

    # Then the service bypasses both cache operations and uses the fresh provider result.
    assert first.status is HarmonizeStatus.SUCCEEDED
    assert second.status is HarmonizeStatus.SUCCEEDED
    assert read_tabular(second_output).rows == [["Matched"]]
    assert cache.loaded_keys == loaded_keys_before_second_run
    assert cache.save_calls == 1


def test_service_rejects_provider_value_outside_permissible_values(tmp_path: Path) -> None:
    # Given a provider that violates the permissible-value contract.
    source = tmp_path / "source.csv"
    source.write_text("diagnosis\nunknown\n", encoding="utf-8")
    output = tmp_path / "output.csv"

    class InvalidProvider:
        def harmonize(
            self,
            requests: tuple[TermHarmonizationRequest, ...],
        ) -> tuple[TermHarmonizationResponse, ...]:
            return tuple(
                TermHarmonizationResponse(
                    matched_value="Not Allowed",
                    match_fidelity=MatchFidelity.STRONG,
                )
                for _request in requests
            )

    service = FileHarmonizationService(InvalidProvider())

    # When the real service receives the invalid provider result.
    result = service.run(
        file_path=source,
        data_model_version=MODEL_VERSION,
        prepared_manifest=_manifest(),
        column_pv_sets=ColumnPvSets({
            column_key_for_index(0): frozenset({"Known"}),
        }),
        output_path=output,
    )

    # Then the job fails without publishing an invalid output.
    assert result.status is HarmonizeStatus.FAILED
    assert not output.exists()
