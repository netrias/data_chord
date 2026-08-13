"""Agentic harmonization adapter behavior."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from netrias_client import read_tabular

from src.domain.columns import column_key_for_index
from src.domain.harmonization import HarmonizeStatus, MatchFidelity
from src.domain.manifest import ColumnMappingManifest
from src.integrations.agentic_harmonize import AgenticHarmonizeConfig, AgenticHarmonizeService
from src.persistence.manifest_reader import read_manifest_parquet
from src.persistence.pv_manifest_store import ColumnPvSets


def _manifest() -> ColumnMappingManifest:
    return ColumnMappingManifest.from_payload_strict({
        "column_mappings": {
            "col_0000": {"cde_key": "diagnosis", "cde_id": 1},
            "col_0001": {"cde_key": "treatment", "cde_id": 2},
        }
    })


def _prediction(term: str, match: str, fidelity: str = "strong") -> SimpleNamespace:
    return SimpleNamespace(
        prediction=SimpleNamespace(
            input_term=term,
            predicted_match=match,
            match_fidelity=fidelity,
        )
    )


def test_agentic_defaults_use_bedrock_gpt_56_with_high_reasoning() -> None:
    config = AgenticHarmonizeConfig(region="us-east-2")

    assert config.explorer_model.name == "gpt-5.6-luna"
    assert config.shortlister_model.name == "gpt-5.6-luna"
    assert config.selector_model.name == "gpt-5.6-sol"
    assert config.reasoning_effort == "high"
    assert config.exploration_turns == 10
    assert config.max_workers == 50


def test_agentic_harmonization_collapses_duplicates_only_within_each_column(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.csv"
    source.write_text("diagnosis,treatment\nsame,same\nsame,same\n", encoding="utf-8")
    output = tmp_path / "output.csv"
    calls: list[tuple[str, str]] = []
    model_calls: list[tuple[str, str, str]] = []
    client_calls: list[tuple[str, object, object]] = []

    def fake_harmonize(_client, _index, term: str, **kwargs: object) -> SimpleNamespace:
        context = str(kwargs["context"])
        calls.append((term, context))
        model_calls.append((
            kwargs["explorer_model"].name,  # type: ignore[union-attr]
            kwargs["shortlister_model"].name,  # type: ignore[union-attr]
            kwargs["selector_model"].name,  # type: ignore[union-attr]
        ))
        match = "Diagnosis Match" if "diagnosis" in context else "Treatment Match"
        return _prediction(term, match)

    monkeypatch.setattr("src.integrations.agentic_harmonize.harmonize_term", fake_harmonize)
    def fake_client(region: str, *, provider: object, reasoning_effort: object) -> object:
        client_calls.append((region, provider, reasoning_effort))
        return object()

    monkeypatch.setattr("src.integrations.agentic_harmonize.make_provider_client", fake_client)
    service = AgenticHarmonizeService(AgenticHarmonizeConfig(region="us-east-2", max_workers=2))

    result = service.run(
        file_path=source,
        data_model_key="CCDI",
        external_version_number="1",
        prepared_manifest=_manifest(),
        column_pv_sets=ColumnPvSets({
            column_key_for_index(0): frozenset({"Diagnosis Match"}),
            column_key_for_index(1): frozenset({"Treatment Match"}),
        }),
        output_path=output,
    )

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
    source = tmp_path / "source.csv"
    source.write_text("diagnosis,treatment\nunknown,keep\n", encoding="utf-8")
    output = tmp_path / "output.csv"
    monkeypatch.setattr(
        "src.integrations.agentic_harmonize.harmonize_term",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("provider must not be called")),
    )
    service = AgenticHarmonizeService(AgenticHarmonizeConfig(region="us-east-2"))

    result = service.run(
        file_path=source,
        data_model_key="CCDI",
        external_version_number="1",
        prepared_manifest=_manifest(),
        column_pv_sets=ColumnPvSets({
            column_key_for_index(0): frozenset(),
            column_key_for_index(1): None,
        }),
        output_path=output,
    )

    assert result.status is HarmonizeStatus.SUCCEEDED
    assert read_tabular(output).rows == [["unknown", "keep"]]
    summary = read_manifest_parquet(result.manifest_path) if result.manifest_path else None
    assert summary is not None
    assert all(row.match_fidelity is MatchFidelity.NONE for row in summary.rows)


def test_agentic_no_match_keeps_the_source_value(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text("diagnosis,treatment\nunknown,keep\n", encoding="utf-8")
    output = tmp_path / "output.csv"
    monkeypatch.setattr(
        "src.integrations.agentic_harmonize.harmonize_term",
        lambda _client, _index, term, **_kwargs: _prediction(term, "NO_MATCH", "none"),
    )
    monkeypatch.setattr("src.integrations.agentic_harmonize.make_provider_client", lambda *args, **kwargs: object())
    service = AgenticHarmonizeService(AgenticHarmonizeConfig(region="us-east-2", max_workers=2))

    result = service.run(
        file_path=source,
        data_model_key="CCDI",
        external_version_number="1",
        prepared_manifest=_manifest(),
        column_pv_sets=ColumnPvSets({
            column_key_for_index(0): frozenset({"Known"}),
            column_key_for_index(1): frozenset({"Kept"}),
        }),
        output_path=output,
    )

    assert result.status is HarmonizeStatus.SUCCEEDED
    assert read_tabular(output).rows == [["unknown", "keep"]]
    summary = read_manifest_parquet(result.manifest_path) if result.manifest_path else None
    assert summary is not None
    assert all(row.match_fidelity is MatchFidelity.NONE for row in summary.rows)


def test_agentic_harmonization_fails_without_output_when_one_term_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.csv"
    source.write_text("diagnosis,treatment\ngood,bad\n", encoding="utf-8")
    output = tmp_path / "output.csv"

    def fake_harmonize(_client, _index, term: str, **_kwargs: object) -> SimpleNamespace:
        if term == "bad":
            raise RuntimeError("provider secret")
        return _prediction(term, "Diagnosis Match")

    monkeypatch.setattr("src.integrations.agentic_harmonize.harmonize_term", fake_harmonize)
    monkeypatch.setattr("src.integrations.agentic_harmonize.make_provider_client", lambda *args, **kwargs: object())
    service = AgenticHarmonizeService(AgenticHarmonizeConfig(region="us-east-2", max_workers=2))

    result = service.run(
        file_path=source,
        data_model_key="CCDI",
        external_version_number="1",
        prepared_manifest=_manifest(),
        column_pv_sets=ColumnPvSets({
            column_key_for_index(0): frozenset({"Diagnosis Match"}),
            column_key_for_index(1): frozenset({"Treatment Match"}),
        }),
        output_path=output,
    )

    assert result.status is HarmonizeStatus.FAILED
    assert result.detail == "Harmonization provider failed."
    assert result.manifest_path is None
    assert result.output_path is None
    assert not output.exists()
    assert not output.with_name("output.manifest.parquet").exists()
