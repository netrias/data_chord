"""Test transformation history feature in Stage 5 summary response."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from src.domain.storage import UploadStorage
from tests.conftest import (
    create_harmonized_csv,
    create_test_manifest_parquet,
    upload_file,
)

pytestmark = pytest.mark.asyncio


def _create_manifest_with_history(
    storage: UploadStorage,
    file_id: str,
    original_value: str,
    ai_value: str,
    manual_overrides: list[dict[str, str | None]],
) -> Path:
    """Create a manifest with specific transformation history for testing."""
    manifest_rows = [{
        "job_id": f"test-job-{file_id}",
        "column_id": 0,
        "column_name": "test_column",
        "to_harmonize": original_value,
        "top_harmonization": ai_value,
        "ontology_id": None,
        "top_harmonizations": [ai_value] if ai_value else [],
        "confidence_score": 0.85,
        "error": None,
        "row_indices": [0],
        "manual_overrides": manual_overrides,
    }]

    manifest_dir = storage.manifest_dir
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{file_id}_harmonization.parquet"
    return create_test_manifest_parquet(manifest_path, manifest_rows)


class TestTransformationHistoryContract:
    """POST /stage-5/summary returns transformation history for each mapping."""

    async def test_term_mapping_includes_history_field(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Each term mapping includes a history array."""

        # Given: An uploaded file with a manifest containing AI changes
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(meta.saved_path, {0: {"therapeutic_agents": "Harmonized Agent"}})
        _create_manifest_with_history(
            storage=temp_storage,
            file_id=file_id,
            original_value="Original Agent",
            ai_value="Harmonized Agent",
            manual_overrides=[],
        )

        # When: Summary is requested
        response = await app_client.post(
            "/stage-5/summary",
            json={"file_id": file_id},
        )

        # Then: term_mappings exist and each has a history field
        assert response.status_code == 200
        data = response.json()
        assert "term_mappings" in data
        assert len(data["term_mappings"]) > 0
        for mapping in data["term_mappings"]:
            assert "history" in mapping, "Each term mapping should include history"
            assert isinstance(mapping["history"], list)

    async def test_history_includes_original_and_ai_steps(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """History shows original value and AI suggestion as separate steps."""

        # Given: A manifest where AI changed the value
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(meta.saved_path, {0: {"therapeutic_agents": "AI Suggestion"}})
        _create_manifest_with_history(
            storage=temp_storage,
            file_id=file_id,
            original_value="Original Value",
            ai_value="AI Suggestion",
            manual_overrides=[],
        )

        # When: Summary is requested
        response = await app_client.post(
            "/stage-5/summary",
            json={"file_id": file_id},
        )

        # Then: History contains original and AI steps
        assert response.status_code == 200
        mappings = response.json()["term_mappings"]
        assert len(mappings) > 0

        history = mappings[0]["history"]
        sources = [step["source"] for step in history]
        assert "original" in sources, "History should include original value step"
        assert "ai" in sources, "History should include AI suggestion step"

    async def test_history_includes_manual_override_with_metadata(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Manual overrides appear in history with user_id and timestamp."""

        # Given: A manifest with a manual override
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(meta.saved_path, {0: {"therapeutic_agents": "User Override"}})
        _create_manifest_with_history(
            storage=temp_storage,
            file_id=file_id,
            original_value="Original Value",
            ai_value="AI Suggestion",
            manual_overrides=[{
                "user_id": "test-user@example.com",
                "timestamp": "2024-01-15T14:30:00Z",
                "value": "User Override",
            }],
        )

        # When: Summary is requested
        response = await app_client.post(
            "/stage-5/summary",
            json={"file_id": file_id},
        )

        # Then: History contains user override with metadata
        assert response.status_code == 200
        mappings = response.json()["term_mappings"]
        history = mappings[0]["history"]

        user_steps = [s for s in history if s["source"] == "user"]
        assert len(user_steps) == 1, "Should have exactly one user override step"

        user_step = user_steps[0]
        assert user_step["value"] == "User Override"
        assert user_step["user_id"] == "test-user@example.com"
        assert user_step["timestamp"] == "2024-01-15T14:30:00Z"

    async def test_history_collapses_consecutive_duplicate_overrides(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Consecutive overrides with same value are collapsed to one step."""

        # Given: A manifest with multiple overrides of the same value
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(meta.saved_path, {0: {"therapeutic_agents": "Same Value"}})
        _create_manifest_with_history(
            storage=temp_storage,
            file_id=file_id,
            original_value="Original",
            ai_value="AI Suggestion",
            manual_overrides=[
                {"user_id": "user1", "timestamp": "2024-01-15T14:30:00Z", "value": "Same Value"},
                {"user_id": "user1", "timestamp": "2024-01-15T14:30:01Z", "value": "Same Value"},
                {"user_id": "user1", "timestamp": "2024-01-15T14:30:02Z", "value": "Same Value"},
            ],
        )

        # When: Summary is requested
        response = await app_client.post(
            "/stage-5/summary",
            json={"file_id": file_id},
        )

        # Then: Only one user override step appears (duplicates collapsed)
        assert response.status_code == 200
        mappings = response.json()["term_mappings"]
        history = mappings[0]["history"]

        user_steps = [s for s in history if s["source"] == "user"]
        assert len(user_steps) == 1, f"Expected 1 user step after dedup, got {len(user_steps)}"

    async def test_history_preserves_distinct_override_values(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Different override values each appear as separate steps."""

        # Given: A manifest with multiple distinct overrides
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(meta.saved_path, {0: {"therapeutic_agents": "Final Value"}})
        _create_manifest_with_history(
            storage=temp_storage,
            file_id=file_id,
            original_value="Original",
            ai_value="AI Suggestion",
            manual_overrides=[
                {"user_id": "user1", "timestamp": "2024-01-15T14:30:00Z", "value": "First Edit"},
                {"user_id": "user1", "timestamp": "2024-01-15T14:31:00Z", "value": "Second Edit"},
                {"user_id": "user1", "timestamp": "2024-01-15T14:32:00Z", "value": "Final Value"},
            ],
        )

        # When: Summary is requested
        response = await app_client.post(
            "/stage-5/summary",
            json={"file_id": file_id},
        )

        # Then: All three distinct values appear in history
        assert response.status_code == 200
        mappings = response.json()["term_mappings"]
        history = mappings[0]["history"]

        user_steps = [s for s in history if s["source"] == "user"]
        assert len(user_steps) == 3, f"Expected 3 distinct user steps, got {len(user_steps)}"

        values = [s["value"] for s in user_steps]
        assert values == ["First Edit", "Second Edit", "Final Value"]

    async def test_history_step_structure(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Each history step has required TransformationStep fields."""

        # Given: A manifest with transformation history
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(meta.saved_path, {0: {"therapeutic_agents": "Changed"}})
        _create_manifest_with_history(
            storage=temp_storage,
            file_id=file_id,
            original_value="Original",
            ai_value="Changed",
            manual_overrides=[],
        )

        # When: Summary is requested
        response = await app_client.post(
            "/stage-5/summary",
            json={"file_id": file_id},
        )

        # Then: Each step has value and source fields
        assert response.status_code == 200
        mappings = response.json()["term_mappings"]
        history = mappings[0]["history"]

        for step in history:
            assert "value" in step, "Each step must have a value"
            assert "source" in step, "Each step must have a source"
            assert step["source"] in ("original", "ai", "user")

    async def test_no_ai_step_when_value_unchanged(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """AI step is omitted when AI suggestion equals original value."""

        # Given: A manifest where AI didn't change the value
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        same_value = "Unchanged Value"
        create_harmonized_csv(meta.saved_path, {0: {"therapeutic_agents": same_value}})
        _create_manifest_with_history(
            storage=temp_storage,
            file_id=file_id,
            original_value=same_value,
            ai_value=same_value,  # Same as original
            manual_overrides=[],
        )

        # When: Summary is requested
        response = await app_client.post(
            "/stage-5/summary",
            json={"file_id": file_id},
        )

        # Then: No AI step in history (only original)
        assert response.status_code == 200
        mappings = response.json()["term_mappings"]
        # Note: unchanged values may not appear in term_mappings at all
        # This test validates the logic when they do appear
        if mappings:
            history = mappings[0]["history"]
            sources = [step["source"] for step in history]
            assert "ai" not in sources, "AI step should be omitted when value unchanged"

    async def test_original_step_has_upload_timestamp(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Original step timestamp uses the file upload time."""

        # Given: An uploaded file with a manifest containing history
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(meta.saved_path, {0: {"therapeutic_agents": "Changed Value"}})
        _create_manifest_with_history(
            storage=temp_storage,
            file_id=file_id,
            original_value="Original Value",
            ai_value="Changed Value",
            manual_overrides=[],
        )

        # When: Summary is requested
        response = await app_client.post(
            "/stage-5/summary",
            json={"file_id": file_id},
        )

        # Then: Original step has a timestamp from the upload time
        assert response.status_code == 200
        mappings = response.json()["term_mappings"]
        assert len(mappings) > 0

        history = mappings[0]["history"]
        original_step = next(s for s in history if s["source"] == "original")
        assert original_step["timestamp"] is not None, "Original step should have upload timestamp"

    async def test_each_step_has_pv_conformance_field(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Every step in history includes is_pv_conformant boolean."""

        # Given: A manifest with original + AI + user steps
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(meta.saved_path, {0: {"therapeutic_agents": "User Override"}})
        _create_manifest_with_history(
            storage=temp_storage,
            file_id=file_id,
            original_value="Original Value",
            ai_value="AI Value",
            manual_overrides=[{
                "user_id": "test-user@example.com",
                "timestamp": "2024-01-15T14:30:00Z",
                "value": "User Override",
            }],
        )

        # When: Summary is requested
        response = await app_client.post(
            "/stage-5/summary",
            json={"file_id": file_id},
        )

        # Then: Every step has is_pv_conformant field
        assert response.status_code == 200
        mappings = response.json()["term_mappings"]
        assert len(mappings) > 0

        history = mappings[0]["history"]
        for step in history:
            assert "is_pv_conformant" in step, f"Step {step['source']} missing is_pv_conformant"
            assert isinstance(step["is_pv_conformant"], bool)

    async def test_conformant_value_marked_true(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Value in PV set has is_pv_conformant=True."""
        # Given: A manifest with a value that will be in the PV set
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(meta.saved_path, {0: {"therapeutic_agents": "Conformant Value"}})
        _create_manifest_with_history(
            storage=temp_storage,
            file_id=file_id,
            original_value="Original",
            ai_value="Conformant Value",
            manual_overrides=[],
        )

        # When: Summary is requested with a mocked PV set containing the value
        with patch("src.stage_5_review_summary.router.ensure_pvs_loaded") as mock_ensure:
            mock_cache = mock_ensure.return_value
            mock_cache.get_pvs_for_column.return_value = frozenset({"Conformant Value"})

            response = await app_client.post(
                "/stage-5/summary",
                json={"file_id": file_id},
            )

        # Then: AI step (with "Conformant Value") is marked conformant
        assert response.status_code == 200
        mappings = response.json()["term_mappings"]
        assert len(mappings) > 0

        history = mappings[0]["history"]
        ai_step = next(s for s in history if s["source"] == "ai")
        assert ai_step["is_pv_conformant"] is True

    async def test_non_conformant_value_marked_false(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Value NOT in PV set has is_pv_conformant=False."""
        # Given: A manifest with values not in the PV set
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(meta.saved_path, {0: {"therapeutic_agents": "Non Conformant"}})
        _create_manifest_with_history(
            storage=temp_storage,
            file_id=file_id,
            original_value="Original",
            ai_value="Non Conformant",
            manual_overrides=[],
        )

        # When: Summary is requested with a mocked PV set NOT containing the values
        with patch("src.stage_5_review_summary.router.ensure_pvs_loaded") as mock_ensure:
            mock_cache = mock_ensure.return_value
            mock_cache.get_pvs_for_column.return_value = frozenset({"Other Value"})

            response = await app_client.post(
                "/stage-5/summary",
                json={"file_id": file_id},
            )

        # Then: Steps with non-conformant values are marked as such
        assert response.status_code == 200
        mappings = response.json()["term_mappings"]
        assert len(mappings) > 0

        history = mappings[0]["history"]
        ai_step = next(s for s in history if s["source"] == "ai")
        assert ai_step["is_pv_conformant"] is False

    async def test_no_pv_set_defaults_conformant(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """When no PV set exists, all steps default to is_pv_conformant=True."""
        # Given: A manifest with transformation history
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(meta.saved_path, {0: {"therapeutic_agents": "Any Value"}})
        _create_manifest_with_history(
            storage=temp_storage,
            file_id=file_id,
            original_value="Original",
            ai_value="Any Value",
            manual_overrides=[],
        )

        # When: Summary is requested with no PV set (returns None)
        with patch("src.stage_5_review_summary.router.ensure_pvs_loaded") as mock_ensure:
            mock_cache = mock_ensure.return_value
            mock_cache.get_pvs_for_column.return_value = None

            response = await app_client.post(
                "/stage-5/summary",
                json={"file_id": file_id},
            )

        # Then: All steps default to conformant (graceful degradation)
        assert response.status_code == 200
        mappings = response.json()["term_mappings"]
        assert len(mappings) > 0

        history = mappings[0]["history"]
        for step in history:
            assert step["is_pv_conformant"] is True, f"Step {step['source']} should default to conformant"
