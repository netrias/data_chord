"""Test transformation history feature in Stage 5 summary response."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from src.domain.columns import column_key_from_string
from src.domain.review_overrides import (
    ReviewOverrideAction,
    ReviewOverrideEvent,
    ReviewOverrides,
    ReviewProgressState,
)
from src.persistence.review_override_store import ReviewOverridesRecord
from src.storage import UploadStorage, VersionToken
from tests.conftest import (
    create_harmonized_csv,
    store_test_harmonization_manifest,
    upload_file,
)

pytestmark = pytest.mark.asyncio


def _create_base_manifest(
    storage: UploadStorage,
    file_id: str,
    original_value: str,
    ai_value: str,
) -> Path:
    """Create immutable Stage 3 facts for a history test."""
    manifest_rows = [{
        "job_id": f"test-job-{file_id}",
        "column_id": 0,
        "column_name": "test_column",
        "to_harmonize": original_value,
        "top_harmonization": ai_value,
        "ontology_id": None,
        "top_harmonizations": [ai_value] if ai_value else [],
        "match_fidelity": "partial",
        "error": None,
        "row_indices": [0],
    }]

    return store_test_harmonization_manifest(storage, file_id, manifest_rows)


def _review_record(
    values: list[str],
    timestamps: list[str],
    original_value: str = "Original Value",
) -> ReviewOverridesRecord:
    events = tuple(
        ReviewOverrideEvent(
            kind=ReviewOverrideAction.SET,
            row_key="1",
            column_key=column_key_from_string("col_0000"),
            original_value=original_value,
            selected_value=value,
            timestamp=datetime.fromisoformat(timestamp.replace("Z", "+00:00")),
        )
        for value, timestamp in zip(values, timestamps, strict=True)
    )
    return ReviewOverridesRecord(
        value=ReviewOverrides(
            file_id="a" * 32,
            created_at=events[0].timestamp,
            updated_at=events[-1].timestamp,
            events=events,
            review_state=ReviewProgressState(),
        ),
        version=VersionToken("test-review-version"),
    )


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
        create_harmonized_csv(temp_storage, file_id, meta.saved_path, {0: {"therapeutic_agents": "Harmonized Agent"}})
        _create_base_manifest(
            storage=temp_storage,
            file_id=file_id,
            original_value="Original Agent",
            ai_value="Harmonized Agent",
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
        create_harmonized_csv(temp_storage, file_id, meta.saved_path, {0: {"therapeutic_agents": "AI Suggestion"}})
        _create_base_manifest(
            storage=temp_storage,
            file_id=file_id,
            original_value="Original Value",
            ai_value="AI Suggestion",
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

    async def test_history_includes_review_decision_timestamp(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Review decisions appear in history with their event timestamp."""

        # Given: immutable Stage 3 facts and one review event.
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(temp_storage, file_id, meta.saved_path, {0: {"therapeutic_agents": "User Override"}})
        _create_base_manifest(
            storage=temp_storage,
            file_id=file_id,
            original_value="Original Value",
            ai_value="AI Suggestion",
        )

        # When: Summary is requested from the v3 event log
        with patch(
            "src.stage_5_review_summary.use_cases.load_readable_review_overrides_record",
            return_value=_review_record(
                ["User Override"],
                ["2024-01-15T14:30:00Z"],
            ),
        ):
            response = await app_client.post(
                "/stage-5/summary",
                json={"file_id": file_id},
            )

        # Then: History contains the v3 user decision
        assert response.status_code == 200
        mappings = response.json()["term_mappings"]
        history = mappings[0]["history"]

        user_steps = [s for s in history if s["source"] == "user"]
        assert len(user_steps) == 1, "Should have exactly one user override step"

        user_step = user_steps[0]
        assert user_step["value"] == "User Override"
        assert user_step["timestamp"] == "2024-01-15T14:30:00+00:00"

    async def test_history_preserves_distinct_review_values(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Different review values each appear as separate steps."""

        # Given: immutable Stage 3 facts and distinct review events.
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(temp_storage, file_id, meta.saved_path, {0: {"therapeutic_agents": "Final Value"}})
        _create_base_manifest(
            storage=temp_storage,
            file_id=file_id,
            original_value="Original",
            ai_value="AI Suggestion",
        )

        # When: Summary is requested
        with patch(
            "src.stage_5_review_summary.use_cases.load_readable_review_overrides_record",
            return_value=_review_record(
                ["First Edit", "Second Edit", "Final Value"],
                [
                    "2024-01-15T14:30:00Z",
                    "2024-01-15T14:31:00Z",
                    "2024-01-15T14:32:00Z",
                ],
                original_value="Original",
            ),
        ):
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
        create_harmonized_csv(temp_storage, file_id, meta.saved_path, {0: {"therapeutic_agents": "Changed"}})
        _create_base_manifest(
            storage=temp_storage,
            file_id=file_id,
            original_value="Original",
            ai_value="Changed",
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
        create_harmonized_csv(temp_storage, file_id, meta.saved_path, {0: {"therapeutic_agents": same_value}})
        _create_base_manifest(
            storage=temp_storage,
            file_id=file_id,
            original_value=same_value,
            ai_value=same_value,  # Same as original
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
        create_harmonized_csv(temp_storage, file_id, meta.saved_path, {0: {"therapeutic_agents": "Changed Value"}})
        _create_base_manifest(
            storage=temp_storage,
            file_id=file_id,
            original_value="Original Value",
            ai_value="Changed Value",
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

    async def test_conformant_value_is_clear(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """A value in the approved set is clear."""
        # Given: A manifest with a value that will be in the PV set
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(temp_storage, file_id, meta.saved_path, {0: {"therapeutic_agents": "Conformant Value"}})
        _create_base_manifest(
            storage=temp_storage,
            file_id=file_id,
            original_value="Original",
            ai_value="Conformant Value",
        )

        # When: Summary is requested with a mocked PV set containing the value
        with patch("src.stage_5_review_summary.use_cases.column_pv_sets") as mock_column_pvs:
            mock_column_pvs.return_value = {"col_0000": frozenset({"Conformant Value"})}
            response = await app_client.post(
                "/stage-5/summary",
                json={"file_id": file_id},
            )

        # Then: the AI step is clear
        assert response.status_code == 200
        mappings = response.json()["term_mappings"]
        assert len(mappings) > 0

        history = mappings[0]["history"]
        ai_step = next(s for s in history if s["source"] == "ai")
        assert ai_step["review_status"] == "clear"

    async def test_non_conformant_value_needs_attention(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """A value outside the approved set needs attention."""
        # Given: A manifest with values not in the PV set
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(temp_storage, file_id, meta.saved_path, {0: {"therapeutic_agents": "Non Conformant"}})
        _create_base_manifest(
            storage=temp_storage,
            file_id=file_id,
            original_value="Original",
            ai_value="Non Conformant",
        )

        # When: Summary is requested with a mocked PV set NOT containing the values
        with patch("src.stage_5_review_summary.use_cases.column_pv_sets") as mock_column_pvs:
            mock_column_pvs.return_value = {"col_0000": frozenset({"Other Value"})}
            response = await app_client.post(
                "/stage-5/summary",
                json={"file_id": file_id},
            )

        # Then: the AI step needs attention
        assert response.status_code == 200
        mappings = response.json()["term_mappings"]
        assert len(mappings) > 0

        history = mappings[0]["history"]
        ai_step = next(s for s in history if s["source"] == "ai")
        assert ai_step["review_status"] == "needs_attention"

    async def test_no_pv_set_is_not_checked(
        self,
        app_client: AsyncClient,
        temp_storage: UploadStorage,
        sample_csv_path: Path,
    ) -> None:
        """Values without an approved set are not checked."""
        # Given: A manifest with transformation history
        file_id = await upload_file(app_client, sample_csv_path)
        meta = temp_storage.load(file_id)
        assert meta is not None
        create_harmonized_csv(temp_storage, file_id, meta.saved_path, {0: {"therapeutic_agents": "Any Value"}})
        _create_base_manifest(
            storage=temp_storage,
            file_id=file_id,
            original_value="Original",
            ai_value="Any Value",
        )

        # When: Summary is requested with no PV set (returns None)
        with patch("src.stage_5_review_summary.use_cases.column_pv_sets") as mock_column_pvs:
            mock_column_pvs.return_value = {"col_0000": None}
            response = await app_client.post(
                "/stage-5/summary",
                json={"file_id": file_id},
            )

        # Then: no history step claims conformance was checked
        assert response.status_code == 200
        mappings = response.json()["term_mappings"]
        assert len(mappings) > 0

        history = mappings[0]["history"]
        for step in history:
            assert step["review_status"] == "not_checked"
