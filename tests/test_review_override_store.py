"""Behavior proof for strict current review-state persistence."""

from __future__ import annotations

import base64
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

import pytest
from httpx import AsyncClient

import src.app.dependencies as dependencies
from src.domain.dataset_workflow_ids import dataset_workflow_id_from_string
from src.persistence.review_override_store import (
    ReviewOverridesUnreadableError,
    load_review_overrides,
)
from src.storage import LocalWorkflowStorage, UploadStorage, UserContext, WorkflowFile
from tests.conftest import (
    create_csv_content,
    create_harmonized_csv,
    create_manifest_for_file,
    upload_content,
)

_FILE_ID = "a" * 32


def _valid_stored_review_state(file_id: str = _FILE_ID) -> dict[str, object]:
    return {
        "schema_version": 1,
        "file_id": file_id,
        "created_at": "2026-08-06T12:00:00+00:00",
        "updated_at": "2026-08-06T12:01:00+00:00",
        "overrides": {
            "1": {
                "col_0000": {
                    "human_value": "Reviewed",
                    "original_value": "Source",
                },
            },
        },
        "review_state": {
            "review_mode": "column",
            "sort_mode": "original",
            "scroll_mode": False,
            "show_case_only_changes": False,
            "show_unchanged_values": False,
            "column_mode": {"current_unit": 1, "batch_size": 4},
            "row_mode": {"current_unit": 1, "batch_size": 5},
        },
    }


def _stored_review_state(
    tmp_path: Path,
    payload: dict[str, object],
) -> tuple[LocalWorkflowStorage, UserContext]:
    storage = LocalWorkflowStorage(tmp_path / "workflow-storage")
    user = UserContext(user_id="reviewer")
    workflow = storage.create_workflow(user, dataset_workflow_id_from_string(_FILE_ID))
    storage.write_json(user, workflow.dataset_workflow_id, WorkflowFile.REVIEW_OVERRIDES, payload)
    return storage, user


def _corrupt_file_identity(payload: dict[str, object]) -> None:
    payload["file_id"] = "b" * 32


def _corrupt_timestamp(payload: dict[str, object]) -> None:
    payload["updated_at"] = "not-a-timestamp"


def _corrupt_cell(payload: dict[str, object]) -> None:
    payload["overrides"] = {
        "1": {
            "col_0000": {"human_value": "Reviewed"},
        },
    }


def _corrupt_progress(payload: dict[str, object]) -> None:
    progress = payload["review_state"]
    assert isinstance(progress, dict)
    payload["review_state"] = {
        **progress,
        "column_mode": {"current_unit": 0, "batch_size": 4},
    }


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param(_corrupt_file_identity, id="file-identity"),
        pytest.param(_corrupt_timestamp, id="required-timestamp"),
        pytest.param(_corrupt_cell, id="complete-cell"),
        pytest.param(_corrupt_progress, id="review-progress"),
    ],
)
def test_store_rejects_corrupt_review_state_as_one_document(
    tmp_path: Path,
    corrupt: Callable[[dict[str, object]], None],
) -> None:
    """A corrupt field cannot silently remove edits or reset progress."""
    payload = deepcopy(_valid_stored_review_state())
    corrupt(payload)
    storage, user = _stored_review_state(tmp_path, payload)

    with pytest.raises(ReviewOverridesUnreadableError, match="review override"):
        load_review_overrides(storage, user, _FILE_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/stage-4/overrides/{file_id}"),
        ("POST", "/stage-5/summary"),
        ("POST", "/stage-5/download"),
    ],
)
async def test_stage4_and_stage5_report_corrupt_saved_review_state_with_recovery(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    method: str,
    path: str,
) -> None:
    """Review and export stop instead of using partial or default review state."""
    file_id = await upload_content(
        app_client,
        create_csv_content([["source"], ["Source"]]),
        "corrupt-review-state.csv",
    )
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})

    storage = dependencies.get_workflow_storage()
    user = dependencies.get_user_context()
    storage.write_json(
        user,
        file_id,
        WorkflowFile.REVIEW_OVERRIDES,
        _valid_stored_review_state(file_id) | {"updated_at": "invalid"},
    )

    if method == "GET":
        response = await app_client.get(path.format(file_id=file_id))
    else:
        response = await app_client.post(path, json={"file_id": file_id})

    assert response.status_code == 409
    assert response.json() == {
        "detail": "The saved review state cannot be read. Return to Stage 3 and run harmonization again.",
    }


@pytest.mark.asyncio
async def test_stage4_save_does_not_replace_corrupt_review_state(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """An autosave cannot turn unreadable state into an incomplete new history."""
    file_id = await upload_content(
        app_client,
        create_csv_content([["source"], ["Source"]]),
        "corrupt-review-save.csv",
    )
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})

    storage = dependencies.get_workflow_storage()
    user = dependencies.get_user_context()
    corrupt_payload = _valid_stored_review_state(file_id) | {"updated_at": "invalid"}
    stored = storage.write_json(
        user,
        file_id,
        WorkflowFile.REVIEW_OVERRIDES,
        corrupt_payload,
    )
    encoded_version = base64.urlsafe_b64encode(stored.version.value.encode()).decode()

    response = await app_client.post(
        "/stage-4/overrides",
        headers={"If-Match": f'"{encoded_version}"'},
        json={
            "file_id": file_id,
            "overrides": {},
            "review_state": {},
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "The saved review state cannot be read. Return to Stage 3 and run harmonization again.",
    }
    unchanged = storage.read_json(user, file_id, WorkflowFile.REVIEW_OVERRIDES)
    assert unchanged is not None
    assert unchanged.data == corrupt_payload
    assert unchanged.version == stored.version
