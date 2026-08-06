"""Behavioral tests for override propagation, whitespace handling, and metrics normalization."""

from __future__ import annotations

import csv
import io
import zipfile
from io import BytesIO
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from src.persistence.manifest_reader import read_manifest_parquet
from src.storage import UploadStorage
from tests.conftest import (
    TEST_TARGET_EXTERNAL_VERSION_NUMBER,
    TEST_TARGET_SCHEMA,
    create_csv_content,
    create_harmonized_csv,
    create_manifest_for_file,
    store_test_completed_harmonization,
    store_test_harmonization_manifest,
    upload_content,
)

pytestmark = pytest.mark.asyncio


def review_state_payload() -> dict[str, object]:
    return {
        "review_mode": "column",
        "sort_mode": "original",
        "scroll_mode": False,
        "show_case_only_changes": False,
        "show_unchanged_values": False,
        "column_mode": {"current_unit": 1, "batch_size": 5},
        "row_mode": {"current_unit": 1, "batch_size": 5},
    }


def _read_downloaded_csv(response_bytes: bytes) -> list[dict[str, str]]:
    with zipfile.ZipFile(BytesIO(response_bytes), "r") as zf:
        csv_name = next(name for name in zf.namelist() if name.endswith(".csv"))
        csv_content = zf.read(csv_name).decode("utf-8")
    return list(csv.DictReader(io.StringIO(csv_content)))


async def _analyze_for_review(app_client: AsyncClient, file_id: str) -> None:
    """Create the canonical mapping plan required by Stage 4 and Stage 5."""
    response = await app_client.post(
        "/stage-1/analyze",
        json={
            "file_id": file_id,
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": TEST_TARGET_EXTERNAL_VERSION_NUMBER,
        },
    )
    assert response.status_code == 200


async def _create_review_overrides(app_client: AsyncClient, payload: object):
    return await app_client.post(
        "/stage-4/overrides",
        headers={"If-None-Match": "*"},
        json=payload,
    )


async def test_stage4_rows_include_grouped_indices(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    rows = [
        ["col_a", "col_b"],
        ["Foo", "Bar"],
        ["Foo", "Bar"],
    ]
    file_id = await upload_content(app_client, create_csv_content(rows), "dupes.csv")
    await _analyze_for_review(app_client, file_id)
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})

    response = await app_client.post("/stage-4/rows", json={"file_id": file_id})

    assert response.status_code == 200
    data = response.json()
    # Column-centric: check that transformations include grouped row indices
    assert len(data["columns"]) >= 1
    col = data["columns"][0]
    assert len(col["transformations"]) == 1  # "Foo" appears in both rows, grouped
    assert col["transformations"][0]["rowIndices"] == [1, 2]


async def test_large_term_keeps_all_rows_through_review_and_export(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """One 60-row term can be selected, saved, and exported without a recovery route."""
    source_rows = [["col_a"], *[["Foo"] for _ in range(60)]]
    file_id = await upload_content(app_client, create_csv_content(source_rows), "large-term.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})

    rows_response = await app_client.post("/stage-4/rows", json={"file_id": file_id})
    assert rows_response.status_code == 200
    transformation = rows_response.json()["columns"][0]["transformations"][0]
    assert transformation["rowIndices"] == list(range(1, 61))

    save_response = await _create_review_overrides(
        app_client,
        {
            "file_id": file_id,
            "overrides": {
                str(row): {
                    "col_0000": {"human_value": "Bar", "original_value": "Foo"},
                }
                for row in transformation["rowIndices"]
            },
            "review_state": review_state_payload(),
        },
    )
    assert save_response.status_code == 200

    download_response = await app_client.post("/stage-5/download", json={"file_id": file_id})
    assert download_response.status_code == 200
    assert [row["col_a"] for row in _read_downloaded_csv(download_response.content)] == [
        "Bar"
    ] * 60


async def test_download_applies_override_per_column_term(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    rows = [
        ["record_id", "col_a", "col_b"],
        ["r1", "Foo", "X"],
        ["r2", "Foo", "Y"],
        ["r3", "Bar", "Foo"],
    ]
    file_id = await upload_content(app_client, create_csv_content(rows), "terms.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    harmonized_path = create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
    store_test_completed_harmonization(temp_storage, file_id, harmonized_path)

    overrides_payload = {
        "file_id": file_id,
        "overrides": {
            "1": {"col_0001": {"human_value": "Baz", "original_value": "Foo"}},
            "2": {"col_0001": {"human_value": "Baz", "original_value": "Foo"}},
        },
        "review_state": review_state_payload(),
    }
    save_response = await _create_review_overrides(app_client, overrides_payload)
    assert save_response.status_code == 200

    download_response = await app_client.post("/stage-5/download", json={"file_id": file_id})
    assert download_response.status_code == 200

    with zipfile.ZipFile(BytesIO(download_response.content), "r") as zf:
        csv_name = next(name for name in zf.namelist() if name.endswith(".csv"))
        csv_content = zf.read(csv_name).decode("utf-8")

    reader = csv.DictReader(io.StringIO(csv_content))
    output_rows = list(reader)
    assert output_rows[0]["col_a"] == "Baz"
    assert output_rows[1]["col_a"] == "Baz"
    assert output_rows[2]["col_a"] == "Bar"
    assert output_rows[2]["col_b"] == "Foo"


async def test_stage4_save_writes_export_overrides_and_summary_audit(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Review saves drive final export while manifest audit rows drive the summary."""

    # Given: a harmonized file has no saved review overrides and no manual audit history
    rows = [["col_a"], ["alpha"]]
    file_id = await upload_content(app_client, create_csv_content(rows), "override-contract.csv")
    await _analyze_for_review(app_client, file_id)
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, {0: {"col_a": "beta"}})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {0: {"col_a": "beta"}})
    manifest_path = temp_storage.load_harmonization_manifest_path(file_id)
    assert manifest_path is not None
    before_summary = await app_client.post("/stage-5/summary", json={"file_id": file_id})
    assert before_summary.status_code == 200
    assert before_summary.json()["column_summaries"][0]["manual_changes"] == 0

    # When: the reviewer saves a manual override
    save_response = await app_client.post(
        "/stage-4/overrides",
        json={
            "file_id": file_id,
            "overrides": {
                "1": {"col_0000": {"human_value": "gamma", "original_value": "alpha"}},
            },
            "review_state": review_state_payload(),
        },
        headers={"If-None-Match": "*"},
    )

    # Then: export uses the JSON review override and summary/history use the manifest audit
    assert save_response.status_code == 200
    saved = read_manifest_parquet(manifest_path)
    assert saved is not None
    assert saved.rows[0].manual_overrides[-1].value == "gamma"

    download_response = await app_client.post("/stage-5/download", json={"file_id": file_id})
    assert download_response.status_code == 200
    output_rows = _read_downloaded_csv(download_response.content)
    assert output_rows[0]["col_a"] == "gamma"

    summary_response = await app_client.post("/stage-5/summary", json={"file_id": file_id})
    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert summary["column_summaries"][0]["manual_changes"] == 1
    user_steps = [
        step
        for mapping in summary["term_mappings"]
        for step in mapping["history"]
        if step["source"] == "user"
    ]
    assert [step["value"] for step in user_steps] == ["gamma"]


async def test_stage4_identical_autosave_does_not_duplicate_summary_audit(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """Retrying the same active review state records one historical decision."""

    # Given: one term is ready for review and has no manual history
    rows = [["col_a"], ["alpha"]]
    file_id = await upload_content(app_client, create_csv_content(rows), "override-idempotence.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, {0: {"col_a": "beta"}})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {0: {"col_a": "beta"}})
    payload = {
        "file_id": file_id,
        "overrides": {
            "1": {"col_0000": {"human_value": "gamma", "original_value": "alpha"}},
        },
        "review_state": review_state_payload(),
    }

    # When: browser autosave sends the identical state twice
    first_response = await _create_review_overrides(app_client, payload)
    version = first_response.headers["etag"]
    second_response = await app_client.post(
        "/stage-4/overrides",
        headers={"If-Match": version},
        json=payload,
    )

    # Then: both compatible saves succeed, but history contains one user decision
    assert first_response.status_code == 200
    assert second_response.status_code == 200
    manifest_path = temp_storage.load_harmonization_manifest_path(file_id)
    assert manifest_path is not None
    saved = read_manifest_parquet(manifest_path)
    assert saved is not None
    assert [override.value for override in saved.rows[0].manual_overrides] == ["gamma"]


async def test_stage4_changed_save_repairs_failed_summary_audit_to_active_state(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """A later choice repairs a missed audit write to the active value."""

    # Given: active review state can save, but the first audit write fails
    rows = [["col_a"], ["alpha"]]
    file_id = await upload_content(app_client, create_csv_content(rows), "override-audit-retry.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, {0: {"col_a": "beta"}})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {0: {"col_a": "beta"}})
    payload = {
        "file_id": file_id,
        "overrides": {
            "1": {"col_0000": {"human_value": "gamma", "original_value": "alpha"}},
        },
        "review_state": review_state_payload(),
    }
    with patch("src.stage_4_review_results.use_cases.add_manual_overrides_batch", return_value=False):
        first_response = await _create_review_overrides(app_client, payload)
    assert first_response.status_code == 200

    # When: the reviewer changes the active choice before the next save
    changed_payload = {
        **payload,
        "overrides": {
            "1": {"col_0000": {"human_value": "delta", "original_value": "alpha"}},
        },
    }
    retry_response = await app_client.post(
        "/stage-4/overrides",
        headers={"If-Match": first_response.headers["etag"]},
        json=changed_payload,
    )

    # Then: the audit matches the current active decision, not the failed old decision
    assert retry_response.status_code == 200
    manifest_path = temp_storage.load_harmonization_manifest_path(file_id)
    assert manifest_path is not None
    saved = read_manifest_parquet(manifest_path)
    assert saved is not None
    assert [override.value for override in saved.rows[0].manual_overrides] == ["delta"]


async def test_stage4_review_version_rejects_stale_save_without_losing_current_state(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """A second tab cannot overwrite a newer review save with an old ETag."""

    # Given: a reviewer creates state and both tabs observe its version
    rows = [["col_a"], ["alpha"]]
    file_id = await upload_content(app_client, create_csv_content(rows), "override-version.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, {0: {"col_a": "beta"}})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {0: {"col_a": "beta"}})
    initial_payload = {
        "file_id": file_id,
        "overrides": {
            "1": {"col_0000": {"human_value": "gamma", "original_value": "alpha"}},
        },
        "review_state": review_state_payload(),
    }
    initial_save = await _create_review_overrides(app_client, initial_payload)
    assert initial_save.status_code == 200
    initial_version = initial_save.headers.get("etag")
    assert initial_version
    loaded = await app_client.get(f"/stage-4/overrides/{file_id}")
    assert loaded.status_code == 200
    assert loaded.headers.get("etag") == initial_version

    newer_payload = {
        **initial_payload,
        "overrides": {
            "1": {"col_0000": {"human_value": "delta", "original_value": "alpha"}},
        },
    }
    newer_save = await app_client.post(
        "/stage-4/overrides",
        headers={"If-Match": initial_version},
        json=newer_payload,
    )
    assert newer_save.status_code == 200
    assert newer_save.headers.get("etag")

    # When: the stale tab tries to save using the version it originally loaded
    stale_payload = {
        **initial_payload,
        "overrides": {
            "1": {"col_0000": {"human_value": "epsilon", "original_value": "alpha"}},
        },
    }
    stale_save = await app_client.post(
        "/stage-4/overrides",
        headers={"If-Match": initial_version},
        json=stale_payload,
    )

    # Then: the caller gets a conflict and the newer active/audit state remains intact
    assert stale_save.status_code == 409
    current = await app_client.get(f"/stage-4/overrides/{file_id}")
    assert current.status_code == 200
    assert current.json()["overrides"]["1"]["col_0000"]["human_value"] == "delta"
    manifest_path = temp_storage.load_harmonization_manifest_path(file_id)
    assert manifest_path is not None
    saved = read_manifest_parquet(manifest_path)
    assert saved is not None
    assert [override.value for override in saved.rows[0].manual_overrides] == ["gamma", "delta"]


async def test_stage4_update_requires_the_loaded_review_version(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """A caller cannot overwrite current review state without its ETag."""

    # Given: existing review state has a current ETag
    rows = [["col_a"], ["alpha"]]
    file_id = await upload_content(app_client, create_csv_content(rows), "override-tokenless.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, {0: {"col_a": "beta"}})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {0: {"col_a": "beta"}})
    payload = {
        "file_id": file_id,
        "overrides": {
            "1": {"col_0000": {"human_value": "gamma", "original_value": "alpha"}},
        },
        "review_state": review_state_payload(),
    }
    first_save = await _create_review_overrides(app_client, payload)
    assert first_save.status_code == 200
    assert first_save.headers.get("etag")

    # When: a caller saves a new choice without the version header
    payload["overrides"] = {
        "1": {"col_0000": {"human_value": "delta", "original_value": "alpha"}},
    }
    tokenless_save = await app_client.post("/stage-4/overrides", json=payload)

    # Then: the API requires the precondition and preserves current state
    assert tokenless_save.status_code == 428
    current = await app_client.get(f"/stage-4/overrides/{file_id}")
    assert current.json()["overrides"]["1"]["col_0000"]["human_value"] == "gamma"


async def test_stage4_preserves_whitespace_values_in_overrides(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    rows = [
        ["col_a"],
        ["  Foo "],
    ]
    file_id = await upload_content(app_client, create_csv_content(rows), "whitespace.csv")
    await _analyze_for_review(app_client, file_id)
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})

    rows_response = await app_client.post("/stage-4/rows", json={"file_id": file_id})
    assert rows_response.status_code == 200
    # Column-centric: get the first transformation from the first column
    columns = rows_response.json()["columns"]
    transformation = columns[0]["transformations"][0]
    assert transformation["originalValue"] == "  Foo "

    overrides_payload = {
        "file_id": file_id,
        "overrides": {
            "1": {"col_0000": {"human_value": "Bar", "original_value": "  Foo "}},
        },
        "review_state": review_state_payload(),
    }
    save_response = await _create_review_overrides(app_client, overrides_payload)
    assert save_response.status_code == 200

    manifest_path = temp_storage.load_harmonization_manifest_path(file_id)
    assert manifest_path is not None
    summary = read_manifest_parquet(manifest_path)
    assert summary is not None
    matching = [row for row in summary.rows if row.column_name == "col_a" and row.to_harmonize == "  Foo "]
    assert matching
    assert matching[0].manual_overrides[-1].value == "Bar"


async def test_stage4_handles_bom_headers(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    content = "\ufeffrecord_id,col_a\nRID-1,Foo\n".encode()
    file_id = await upload_content(app_client, content, "bom.csv")
    await _analyze_for_review(app_client, file_id)
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})

    rows_response = await app_client.post("/stage-4/rows", json={"file_id": file_id})
    assert rows_response.status_code == 200
    # Column-centric: verify columns load correctly with BOM-prefixed headers
    columns = rows_response.json()["columns"]
    assert len(columns) >= 1
    # Verify the col_a column has the expected transformation
    col_a = next((c for c in columns if c["columnLabel"] == "col_a"), None)
    assert col_a is not None
    assert col_a["transformations"][0]["originalValue"] == "Foo"


async def test_summary_counts_case_and_whitespace_changes_exactly(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    rows = [
        ["col_a"],
        ["Foo"],
    ]
    file_id = await upload_content(app_client, create_csv_content(rows), "metrics.csv")
    await _analyze_for_review(app_client, file_id)
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, {0: {"col_a": " foo "}})

    store_test_harmonization_manifest(
        temp_storage,
        file_id,
        [{
            "job_id": f"test-job-{file_id}",
            "column_id": 0,
            "column_name": "col_a",
            "to_harmonize": "Foo",
            "top_harmonization": " foo ",
            "ontology_id": None,
            "top_harmonizations": [" foo "],
            "confidence_score": 0.9,
            "error": None,
            "row_indices": [0],
            "manual_overrides": [],
        }],
    )

    summary_response = await app_client.post("/stage-5/summary", json={"file_id": file_id})
    assert summary_response.status_code == 200
    column_summary = summary_response.json()["column_summaries"][0]
    assert column_summary["changed_distinct_values"] == 1
    assert column_summary["changed_rows"] == 1
    assert column_summary["ai_changes"] == 1
    assert column_summary["manual_changes"] == 0


async def test_summary_history_omits_blank_provider_pass_through(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """A blank recommendation is not presented as a Data Chord decision."""
    file_id = await upload_content(
        app_client,
        create_csv_content([["col_a"], ["Foo"]]),
        "blank-provider-history.csv",
    )
    await _analyze_for_review(app_client, file_id)
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
    store_test_harmonization_manifest(
        temp_storage,
        file_id,
        [{
            "job_id": f"test-job-{file_id}",
            "column_id": 0,
            "column_name": "col_a",
            "to_harmonize": "Foo",
            "top_harmonization": "",
            "ontology_id": None,
            "top_harmonizations": [],
            "confidence_score": 0.9,
            "error": None,
            "row_indices": [0],
            "manual_overrides": [],
        }],
    )

    response = await app_client.post("/stage-5/summary", json={"file_id": file_id})

    assert response.status_code == 200
    mapping = response.json()["term_mappings"][0]
    assert mapping["original_value"] == "Foo"
    assert mapping["final_value"] == "Foo"
    assert mapping["final_value_source"] == "source"
    assert [(step["source"], step["value"]) for step in mapping["history"]] == [
        ("original", "Foo"),
    ]
