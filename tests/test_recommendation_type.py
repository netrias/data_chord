"""Tests for RecommendationType enum and computation logic."""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient

from src.domain.change import RecommendationType
from src.domain.storage import UploadStorage
from src.stage_4_review_results.router import _compute_recommendation_type
from tests.conftest import (
    create_manifest_for_file,
    upload_file,
)


class TestComputeRecommendationType:
    """Pure function tests for _compute_recommendation_type."""

    def test_no_recommendation_when_harmonized_is_none(self) -> None:
        """No recommendation when AI returns no harmonized value."""

        # Given: original value exists but harmonized is None
        original = "Lung Cancer"
        harmonized = None

        # When: computing recommendation type
        result = _compute_recommendation_type(original, harmonized)

        # Then: result is NO_RECOMMENDATION
        assert result == RecommendationType.NO_RECOMMENDATION

    def test_no_recommendation_when_harmonized_is_empty(self) -> None:
        """No recommendation when AI returns empty string."""

        # Given: original value exists but harmonized is empty
        original = "Lung Cancer"
        harmonized = ""

        # When: computing recommendation type
        result = _compute_recommendation_type(original, harmonized)

        # Then: result is NO_RECOMMENDATION
        assert result == RecommendationType.NO_RECOMMENDATION

    def test_no_recommendation_when_harmonized_is_whitespace_only(self) -> None:
        """Whitespace-only harmonized value is treated as NO_RECOMMENDATION.

        If AI returns only whitespace, that's effectively no useful recommendation.
        """

        # Given: original value exists, harmonized is whitespace-only
        original = "Lung Cancer"
        harmonized = "   "

        # When: computing recommendation type
        result = _compute_recommendation_type(original, harmonized)

        # Then: result is NO_RECOMMENDATION
        assert result == RecommendationType.NO_RECOMMENDATION

    def test_ai_changed_when_values_differ(self) -> None:
        """AI_CHANGED when harmonized differs from original."""

        # Given: original and harmonized are different
        original = "lung cancer"
        harmonized = "Lung Cancer"

        # When: computing recommendation type
        result = _compute_recommendation_type(original, harmonized)

        # Then: result is AI_CHANGED
        assert result == RecommendationType.AI_CHANGED

    def test_ai_unchanged_when_values_match(self) -> None:
        """AI_UNCHANGED when AI explicitly kept the original value."""

        # Given: original and harmonized are identical
        original = "Lung Cancer"
        harmonized = "Lung Cancer"

        # When: computing recommendation type
        result = _compute_recommendation_type(original, harmonized)

        # Then: result is AI_UNCHANGED
        assert result == RecommendationType.AI_UNCHANGED

    def test_ai_changed_when_whitespace_differs(self) -> None:
        """AI_CHANGED when whitespace differs (whitespace is semantically significant)."""

        # Given: values differ only in surrounding whitespace
        original = "Lung Cancer"
        harmonized = "  Lung Cancer  "

        # When: computing recommendation type
        result = _compute_recommendation_type(original, harmonized)

        # Then: result is AI_CHANGED (whitespace is significant, no stripping)
        assert result == RecommendationType.AI_CHANGED

    def test_ai_changed_when_original_is_none(self) -> None:
        """AI_CHANGED when original was empty but AI provided value."""

        # Given: original is None, harmonized has value
        original = None
        harmonized = "Lung Cancer"

        # When: computing recommendation type
        result = _compute_recommendation_type(original, harmonized)

        # Then: result is AI_CHANGED (AI provided something new)
        assert result == RecommendationType.AI_CHANGED

    def test_ai_changed_when_original_is_empty(self) -> None:
        """AI_CHANGED when original was empty but AI provided value."""

        # Given: original is empty string, harmonized has value
        original = ""
        harmonized = "Lung Cancer"

        # When: computing recommendation type
        result = _compute_recommendation_type(original, harmonized)

        # Then: result is AI_CHANGED
        assert result == RecommendationType.AI_CHANGED


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


def _find_changed_transformation(columns: list[dict], column_key: str) -> dict | None:
    """Find first transformation where original != harmonized for given column."""
    for col in columns:
        if col["columnKey"] == column_key:
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
