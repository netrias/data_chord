"""Workflow proof that missing source cells are retained but never processed."""

import asyncio
import csv
import gzip
import io
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from httpx import AsyncClient
from netrias_client import TabularFormat, dataset_from_rows, read_tabular, write_tabular

from src.domain.column_profile import DistinctValue, build_column_profile
from src.domain.columns import column_key_for_index
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.harmonization import HarmonizeStatus, MatchFidelity
from src.domain.harmonization_cache import HarmonizationCacheEntry, HarmonizationCacheKey
from src.domain.manifest import ColumnMappingManifest, ManifestRow
from src.integrations.harmonize import (
    FileHarmonizationService,
    TermHarmonizationRequest,
    TermHarmonizationResponse,
)
from src.persistence.manifest_reader import read_manifest_parquet
from src.persistence.manifest_writer import write_manifest_parquet
from src.persistence.pv_manifest_store import ColumnPvSets


class _MockProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[TermHarmonizationRequest, ...]] = []

    def harmonize(
        self,
        requests: tuple[TermHarmonizationRequest, ...],
    ) -> tuple[TermHarmonizationResponse, ...]:
        self.calls.append(requests)
        return tuple(
            TermHarmonizationResponse("Breast Cancer", MatchFidelity.STRONG)
            if request.input_term == "breast ca"
            else TermHarmonizationResponse(None, MatchFidelity.NONE)
            for request in requests
        )


class _MockCache:
    def __init__(self) -> None:
        self.loads: list[list[HarmonizationCacheKey]] = []
        self.saves: list[list[HarmonizationCacheEntry]] = []

    def load_many(
        self,
        keys: Sequence[HarmonizationCacheKey],
    ) -> Mapping[HarmonizationCacheKey, HarmonizationCacheEntry]:
        self.loads.append(list(keys))
        return {}

    def save_many(self, entries: Sequence[HarmonizationCacheEntry]) -> None:
        self.saves.append(list(entries))


@pytest.mark.parametrize("source_format", list(TabularFormat))
@pytest.mark.parametrize("missing_only", [False, True])
def test_missing_cells_survive_file_workflow_without_term_results(
    tmp_path: Path,
    source_format: TabularFormat,
    missing_only: bool,
) -> None:
    # Given: missing cells include repeated blanks and several whitespace forms.
    values = ["", "  ", "\t", "\n", "\u0085", "\u00a0", ""]
    if not missing_only:
        values[2:2] = ["breast ca", "Diabetes", " Lung Cancer "]
    source = tmp_path / f"source{source_format.suffix}"
    write_tabular(
        source,
        dataset_from_rows(
            headers=["record_id", "diagnosis"],
            rows=[[f"RID-{index}", value] for index, value in enumerate(values)],
            source_format=source_format,
        ),
    )
    provider = _MockProvider()
    cache = _MockCache()
    mapping = ColumnMappingManifest.from_payload_strict(
        {
            "column_mappings": {"col_0001": {"cde_key": "primary_diagnosis", "cde_id": 1}},
        }
    )

    # When: the real file service reads, harmonizes, writes, and persists results.
    result = FileHarmonizationService(provider, cache=cache).run(
        file_path=source,
        data_model_version=DataModelVersionReference("gc", "11.0.4"),
        prepared_manifest=mapping,
        column_pv_sets=ColumnPvSets({column_key_for_index(1): frozenset({"Diabetes", "Breast Cancer"})}),
    )

    # Then: full output rows retain the missing cells and only present terms are recorded.
    assert result.status is HarmonizeStatus.SUCCEEDED
    assert result.output_path is not None
    expected = ["Breast Cancer" if value == "breast ca" else value for value in values]
    assert read_tabular(result.output_path).rows == [[f"RID-{index}", value] for index, value in enumerate(expected)]
    assert result.manifest_path is not None
    manifest = read_manifest_parquet(result.manifest_path)
    assert manifest is not None
    expected_terms = [] if missing_only else ["breast ca", "Diabetes", " Lung Cancer "]
    assert [(row.to_harmonize, row.row_indices) for row in manifest.rows] == [
        (term, [values.index(term)]) for term in expected_terms
    ]
    assert (manifest.total_terms, manifest.changed_terms) == (len(expected_terms), int(not missing_only))
    if missing_only:
        assert provider.calls == []
        assert cache.loads == []
        assert cache.saves == []
    else:
        assert [[request.input_term for request in call] for call in provider.calls] == [
            ["breast ca", " Lung Cancer "],
        ]
        assert [[key.source_value for key in call] for call in cache.loads] == [
            ["breast ca", " Lung Cancer "],
        ]
        assert [[entry.key.source_value for entry in call] for call in cache.saves] == [
            ["breast ca", " Lung Cancer "],
        ]


def test_saved_manifest_omits_missing_sources_even_with_a_saved_replacement(tmp_path: Path) -> None:
    # Given: a stored manifest contains missing sources alongside a present source.
    rows = [
        ManifestRow(
            "job",
            0,
            "diagnosis",
            source,
            "Diabetes",
            "primary_diagnosis",
            ["Diabetes"],
            MatchFidelity.STRONG,
            None,
            [index],
        )
        for index, source in enumerate(["", "  ", "\t", "Diabetes"])
    ]
    path = tmp_path / "stored.parquet"
    assert write_manifest_parquet(path, rows)

    # When: the result boundary reads the saved manifest.
    manifest = read_manifest_parquet(path)

    # Then: the complete parsed result contains only the present source, at its real row.
    assert manifest is not None
    assert manifest.rows == [rows[3]]
    assert (manifest.total_terms, manifest.changed_terms) == (1, 0)


def test_column_profile_counts_whitespace_as_missing_without_trimming_present_text() -> None:
    # Given: missing cells and a padded but meaningful value share a column.
    values = [None, "", "  ", "\t", "\u0085", "\u001c", "\ufeff", " Lung Cancer "]

    # When: the column profile is built for mapping and completeness.
    profile = build_column_profile("col_0000", values)

    # Then: only real text is listed; missing cells still contribute to dataset size.
    assert profile.total_rows == 8
    assert profile.null_count == 6
    assert profile.distinct_values == (
        DistinctValue("\ufeff", 1),
        DistinctValue(" Lung Cancer ", 1),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_only", [False, True])
async def test_real_api_job_retains_missing_cells_and_has_no_missing_term_results(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    missing_only: bool,
) -> None:
    # Given: the real shared job service has no-cost recording provider and cache.
    import src.app.dependencies as dependencies

    api_key = "missing-values-api-key-at-least-32-bytes"
    monkeypatch.setenv("DATA_CHORD_API_KEY", api_key)
    provider = _MockProvider()
    cache = _MockCache()
    monkeypatch.setattr(dependencies, "_harmonize_service", FileHarmonizationService(provider, cache=cache))
    values = ["", "   ", "\t", ""]
    if not missing_only:
        values[1:1] = ["breast ca", "Diabetes", " Lung Cancer "]
    envelope = {
        "schemaVersion": "1.0",
        "data_commons_key": "test-data-model",
        "external_version_number": "11.0.4",
        "use_cache": True,
        "document": {
            "name": "missing.csv",
            "sheetName": None,
            "header": ["diagnosis"],
            "rows": [[value] for value in values],
        },
        "column_mappings": [
            {
                "column_name": "diagnosis",
                "cde_key": "primary_diagnosis",
                "cde_id": 42,
                "harmonization": "harmonizable",
                "alternatives": [],
            }
        ],
    }

    # When: a caller submits, polls, and downloads both real artifacts.
    submitted = await app_client.post(
        "/api/v1/jobs/harmonize",
        headers={"x-api-key": api_key, "content-type": "application/octet-stream"},
        content=gzip.compress(json.dumps(envelope).encode()),
    )
    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()["job_id"]
    status_response = await app_client.get(f"/api/v1/jobs/{job_id}", headers={"x-api-key": api_key})
    for _ in range(100):
        assert status_response.status_code == 200, status_response.text
        if status_response.json()["status"] != "QUEUED":
            break
        await asyncio.sleep(0.01)
        status_response = await app_client.get(f"/api/v1/jobs/{job_id}", headers={"x-api-key": api_key})
    status = status_response.json()
    assert set(status) == {"status", "final_url", "manifest_url"}
    assert status["status"] == "SUCCEEDED", status
    final = await app_client.get(status["final_url"])
    manifest_response = await app_client.get(status["manifest_url"])

    # Then: missing cells survive in their positions but never become result terms.
    assert final.status_code == 200
    assert manifest_response.status_code == 200
    assert list(csv.reader(io.StringIO(final.text))) == [
        ["diagnosis"],
        *[["Breast Cancer" if value == "breast ca" else value] for value in values],
    ]
    manifest = pq.read_table(io.BytesIO(manifest_response.content))
    expected_terms = [] if missing_only else ["breast ca", "Diabetes", " Lung Cancer "]
    assert manifest.column("to_harmonize").to_pylist() == expected_terms
    assert manifest.column("row_indices").to_pylist() == [[values.index(term)] for term in expected_terms]
    expected_work = [] if missing_only else [["breast ca", " Lung Cancer "]]
    assert [[request.input_term for request in call] for call in provider.calls] == expected_work
    assert [[key.source_value for key in call] for call in cache.loads] == expected_work
    assert [[entry.key.source_value for entry in call] for call in cache.saves] == expected_work

    # The browser-facing projection must also report successful zero-term jobs with file context.
    summary_response = await app_client.get(
        f"/stage-3/jobs/{job_id}?file_id={job_id}",
        headers={"x-data-chord-user-id": "programmatic-api"},
    )
    assert summary_response.status_code == 200, summary_response.text
    summary = summary_response.json()["manifest_summary"]
    assert (summary["total_terms"], summary["source_row_count"]) == (len(expected_terms), len(values))
    assert [column["total_rows"] for column in summary["column_breakdowns"]] == ([] if missing_only else [len(values)])
