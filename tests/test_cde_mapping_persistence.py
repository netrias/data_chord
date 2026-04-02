"""Feature tests for CDE mapping persistence and download zip inclusion.

Tests the user journey: Stage 2 produces column-to-CDE mapping decisions;
Stage 3 persists them; Stage 5 includes the JSON file in the download zip.
"""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

from httpx import AsyncClient

import src.domain.dependencies as _deps
from src.domain.cde import ColumnMappingDecision
from src.domain.cde_mapping_persistence import load_cde_mapping_json, save_cde_mapping
from src.domain.storage import FileStore, LocalStorageBackend, UploadStorage
from tests.conftest import (
    TEST_TARGET_SCHEMA,
    create_csv_content,
    create_harmonized_csv,
    create_manifest_for_file,
    upload_content,
)

_SAMPLE_DECISIONS: list[ColumnMappingDecision] = [
    {
        "column_name": "primary_diagnosis",
        "cde_name": "primary_diagnosis",
        "cde_id": 2,
        "cde_description": "Primary Diagnosis",
        "method": "ai_recommendation",
    },
    {
        "column_name": "therapeutic_agents",
        "cde_name": None,
        "cde_id": None,
        "cde_description": None,
        "method": "user_override",
    },
]


class TestSaveCdeMappingRoundtrip:
    """CDE mapping documents survive a save/load roundtrip with all fields intact."""

    def test_save_cde_mapping_roundtrip(self, tmp_path: Path) -> None:
        """
        Given: No mapping file written for the file_id
        When: save_cde_mapping is called then load_cde_mapping_json is called
        Then: JSON contains all fields from the decisions; schema_name and version_label match
        """
        # Given
        file_id = "abc12345"
        original = _deps._file_store
        _deps._file_store = FileStore(LocalStorageBackend(tmp_path / "files"))
        try:
            assert load_cde_mapping_json(file_id) is None, "No file should exist before save"

            # When
            save_cde_mapping(file_id, _SAMPLE_DECISIONS, "test_schema", "v2.1")

            # Then
            result_json = load_cde_mapping_json(file_id)
        finally:
            _deps._file_store = original

        assert result_json is not None
        result = json.loads(result_json)
        assert result["file_id"] == file_id
        assert result["schema_name"] == "test_schema"
        assert result["version_label"] == "v2.1"
        assert len(result["ai_mapped"]) == 1
        assert result["ai_mapped"][0]["column_name"] == "primary_diagnosis"
        assert result["ai_mapped"][0]["cde_name"] == "primary_diagnosis"
        assert result["user_overrides"] == []
        assert result["unmapped_columns"] == ["therapeutic_agents"]

    def test_none_version_label_serializes_as_json_null(self, tmp_path: Path) -> None:
        """
        Given: version_label is None (API unavailable case)
        When: save_cde_mapping is called
        Then: JSON field is null, not the string "None"
        """
        file_id = "abc12345"
        original = _deps._file_store
        _deps._file_store = FileStore(LocalStorageBackend(tmp_path / "files"))
        try:
            save_cde_mapping(file_id, _SAMPLE_DECISIONS, "test_schema", None)
            result_json = load_cde_mapping_json(file_id)
        finally:
            _deps._file_store = original

        assert result_json is not None
        result = json.loads(result_json)
        assert result["version_label"] is None
        assert '"None"' not in result_json
        assert "ai_mapped" in result


class TestCdeMappingWrittenAfterHarmonize:
    """Stage 3 persists the mapping decisions supplied in HarmonizeRequest."""

    async def test_cde_mapping_written_after_harmonize(
        self,
        app_client: AsyncClient,
        tmp_path: Path,
    ) -> None:
        """
        Given: Stage 3 harmonize endpoint receives mapping_decisions in the payload
        When: Harmonization completes
        Then: FileType.COLUMN_MAPPING exists for the file_id with ai_mapped and unmapped_columns populated
        """
        # Given: an uploaded and analyzed file
        rows = [["col_a", "col_b"], ["alpha", "red"]]
        file_id = await upload_content(app_client, create_csv_content(rows), "test.csv")
        await app_client.post(
            "/stage-1/analyze",
            json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
        )

        original = _deps._file_store
        _deps._file_store = FileStore(LocalStorageBackend(tmp_path / "files"))
        try:
            assert load_cde_mapping_json(file_id) is None, "No mapping file before harmonize"

            # When: harmonize is triggered with mapping decisions
            response = await app_client.post(
                "/stage-3/harmonize",
                json={
                    "file_id": file_id,
                    "target_schema": TEST_TARGET_SCHEMA,
                    "manual_overrides": {},
                    "mapping_decisions": _SAMPLE_DECISIONS,
                },
            )
            assert response.status_code == 200

            # Then: the mapping file exists and contains the submitted decisions
            result_json = load_cde_mapping_json(file_id)
            assert result_json is not None
            result = json.loads(result_json)
            assert result["file_id"] == file_id
            assert result["schema_name"] == TEST_TARGET_SCHEMA
            assert result["ai_mapped"][0]["column_name"] == "primary_diagnosis"
            assert "therapeutic_agents" in result["unmapped_columns"]
        finally:
            _deps._file_store = original


class TestCdeMappingInDownloadZip:
    """Stage 5 includes the CDE mapping JSON in the download zip when available."""

    async def test_cde_mapping_included_in_zip(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        tmp_path: Path,
    ) -> None:
        """
        Given: Harmonization completed with mapping_decisions and a mapping file is on disk
        When: POST /stage-5/download is called
        Then: Zip contains *_cde_mapping.json; zip has exactly 3 entries
        """
        # Given: full flow through Stages 1–3 with mapping decisions
        rows = [["primary_diagnosis"], ["adenocarcinoma"]]
        file_id = await upload_content(app_client, create_csv_content(rows), "diag.csv")
        await app_client.post(
            "/stage-1/analyze",
            json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
        )

        original = _deps._file_store
        _deps._file_store = FileStore(LocalStorageBackend(tmp_path / "files"))
        try:
            await app_client.post(
                "/stage-3/harmonize",
                json={
                    "file_id": file_id,
                    "target_schema": TEST_TARGET_SCHEMA,
                    "manual_overrides": {},
                    "mapping_decisions": _SAMPLE_DECISIONS,
                },
            )

            meta = temp_storage.load(file_id)
            assert meta is not None
            create_harmonized_csv(meta.saved_path, {})
            create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})

            # When: download is triggered
            response = await app_client.post("/stage-5/download", json={"file_id": file_id})
            assert response.status_code == 200
        finally:
            _deps._file_store = original

        # Then: zip contains cde_mapping.json as the third entry
        with zipfile.ZipFile(BytesIO(response.content)) as zf:
            names = zf.namelist()
            mapping_entries = [n for n in names if n.endswith("_cde_mapping.json")]
            assert len(mapping_entries) == 1, f"Expected one cde_mapping.json, got: {names}"
            assert len(names) == 3, f"Expected 3 zip entries (csv, manifest, mapping), got: {names}"

            result = json.loads(zf.read(mapping_entries[0]).decode("utf-8"))
            assert result["file_id"] == file_id
            assert result["schema_name"] == TEST_TARGET_SCHEMA
            total = len(result["ai_mapped"]) + len(result["user_overrides"]) + len(result["unmapped_columns"])
            assert total == 2

    async def test_zip_succeeds_without_mapping_file(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        tmp_path: Path,
    ) -> None:
        """
        Given: No mapping file on disk (old session — no mapping_decisions in request)
        When: POST /stage-5/download is called
        Then: Zip is returned with exactly 2 entries; no cde_mapping entry
        """
        # Given: harmonization without mapping_decisions (backward-compat case)
        rows = [["primary_diagnosis"], ["adenocarcinoma"]]
        file_id = await upload_content(app_client, create_csv_content(rows), "diag.csv")
        await app_client.post(
            "/stage-1/analyze",
            json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
        )

        original = _deps._file_store
        _deps._file_store = FileStore(LocalStorageBackend(tmp_path / "files"))
        try:
            await app_client.post(
                "/stage-3/harmonize",
                json={
                    "file_id": file_id,
                    "target_schema": TEST_TARGET_SCHEMA,
                    "manual_overrides": {},
                    # No mapping_decisions field — simulates an old client
                },
            )

            meta = temp_storage.load(file_id)
            assert meta is not None
            create_harmonized_csv(meta.saved_path, {})
            create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})

            # When: download triggered
            response = await app_client.post("/stage-5/download", json={"file_id": file_id})
            assert response.status_code == 200
        finally:
            _deps._file_store = original

        # Then: exactly 2 entries — CSV and manifest, no cde_mapping
        with zipfile.ZipFile(BytesIO(response.content)) as zf:
            names = zf.namelist()
            assert not any(n.endswith("_cde_mapping.json") for n in names), f"Unexpected cde_mapping: {names}"
            assert len(names) == 2, f"Expected 2 zip entries (csv, manifest), got: {names}"
