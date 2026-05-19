"""Tests for RecommendationType enum and computation logic."""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient

from src.domain.change import RecommendationType
from src.domain.storage import UploadStorage
from tests.conftest import (
    create_manifest_for_file,
    store_test_harmonization_manifest,
    upload_file,
)


class TestRecommendationTypeEnum:
    """Tests for RecommendationType enum properties."""

    def test_enum_values_are_strings(self) -> None:
        """Enum values serialize to expected string literals."""

        # Given: the enum values

        # When/Then: values match expected strings
        assert RecommendationType.AI_CHANGED.value == "ai_changed"
        assert RecommendationType.AI_UNCHANGED.value == "ai_unchanged"
        assert RecommendationType.NO_RECOMMENDATION.value == "no_recommendation"

    def test_enum_is_string_subclass(self) -> None:
        """RecommendationType is a str enum for JSON serialization."""

        # Given: the enum class

        # When/Then: enum values are string instances
        assert isinstance(RecommendationType.AI_CHANGED, str)
        assert isinstance(RecommendationType.AI_UNCHANGED, str)
        assert isinstance(RecommendationType.NO_RECOMMENDATION, str)


def _find_transformation(columns: list[dict], column_key: str, original_value: str) -> dict | None:
    """Find a transformation by column and original value in the public Stage 4 response."""
    for col in columns:
        if col["columnKey"] == column_key or col.get("columnLabel") == column_key:
            for transformation in col["transformations"]:
                if transformation["originalValue"] == original_value:
                    return transformation
    return None


def _find_changed_transformation(columns: list[dict], column_key: str) -> dict | None:
    """Find first transformation where original != harmonized for given column."""
    for col in columns:
        if col["columnKey"] == column_key or col.get("columnLabel") == column_key:
            for t in col["transformations"]:
                if t["originalValue"] != t["harmonizedValue"]:
                    return t
    return None


class TestStage4RecommendationTypeContract:
    """Contract tests for recommendationType in Stage 4 API responses."""

    async def test_rows_response_includes_recommendation_type(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Stage 4 /rows response includes recommendationType field in cells."""
        # Given: uploaded file with manifest containing harmonization data
        file_id = await upload_file(app_client, sample_csv_path)
        changes = {0: {"primary_diagnosis": "Harmonized Value"}}
        create_manifest_for_file(temp_storage, file_id, sample_csv_path, changes)

        # When: requesting rows from Stage 4
        response = await app_client.post(
            "/stage-4/rows",
            json={"file_id": file_id, "manual_columns": []},
        )

        # Then: response is successful and transformations include recommendationType
        assert response.status_code == 200
        data = response.json()
        assert "columns" in data
        assert len(data["columns"]) > 0

        first_column = data["columns"][0]
        assert "transformations" in first_column
        assert len(first_column["transformations"]) > 0

        first_transformation = first_column["transformations"][0]
        assert "recommendationType" in first_transformation
        assert first_transformation["recommendationType"] in [
            "ai_changed",
            "ai_unchanged",
            "no_recommendation",
        ]

    async def test_recommendation_type_reflects_ai_changed(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """recommendationType is ai_changed when harmonized differs from original."""
        # Given: uploaded file with manifest where AI changed a value
        file_id = await upload_file(app_client, sample_csv_path)
        # Change original value to something different
        changes = {0: {"primary_diagnosis": "Different Harmonized Value"}}
        create_manifest_for_file(temp_storage, file_id, sample_csv_path, changes)

        # When: requesting rows from Stage 4
        response = await app_client.post(
            "/stage-4/rows",
            json={"file_id": file_id, "manual_columns": []},
        )

        # Then: the changed transformation has recommendationType = ai_changed
        assert response.status_code == 200
        data = response.json()

        transformation = _find_changed_transformation(data["columns"], "primary_diagnosis")
        assert transformation is not None, "No ai_changed transformation found"
        assert transformation["recommendationType"] == "ai_changed"

    async def test_recommendation_type_reflects_ai_unchanged(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """recommendationType is ai_unchanged when AI kept original value."""
        # Given: uploaded file with manifest where AI kept original value
        file_id = await upload_file(app_client, sample_csv_path)
        # No changes - AI keeps original values
        changes: dict[int, dict[str, str]] = {}
        create_manifest_for_file(temp_storage, file_id, sample_csv_path, changes)

        # When: requesting rows from Stage 4
        response = await app_client.post(
            "/stage-4/rows",
            json={"file_id": file_id, "manual_columns": []},
        )

        # Then: transformations where original == harmonized have recommendationType = ai_unchanged
        assert response.status_code == 200
        data = response.json()

        found_unchanged = False
        for col in data["columns"]:
            for t in col["transformations"]:
                if t["originalValue"] and t["originalValue"] == t["harmonizedValue"]:
                    assert t["recommendationType"] == "ai_unchanged"
                    found_unchanged = True

        assert found_unchanged, "No ai_unchanged transformations found"

    async def test_recommendation_type_reflects_no_recommendation(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """recommendationType is no_recommendation when AI returns only whitespace."""

        # Given: uploaded file with a manifest row whose AI value has no useful text
        file_id = await upload_file(app_client, sample_csv_path)
        store_test_harmonization_manifest(
            temp_storage,
            file_id,
            [{
                "job_id": f"test-job-{file_id}",
                "column_id": 0,
                "column_name": "primary_diagnosis",
                "to_harmonize": "Lung Cancer",
                "top_harmonization": "   ",
                "ontology_id": None,
                "top_harmonizations": [],
                "confidence_score": 0.4,
                "error": None,
                "row_indices": [0],
                "manual_overrides": [],
            }],
        )

        # When: requesting rows from Stage 4
        response = await app_client.post(
            "/stage-4/rows",
            json={"file_id": file_id, "manual_columns": []},
        )

        # Then: the public response marks the row as no recommendation
        assert response.status_code == 200
        transformation = _find_transformation(response.json()["columns"], "primary_diagnosis", "Lung Cancer")
        assert transformation is not None, "No transformation found for primary_diagnosis"
        assert transformation["recommendationType"] == "no_recommendation"
