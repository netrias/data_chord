"""Behavior proof for strict current review-state persistence."""

from __future__ import annotations

import base64
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

import pytest
from httpx import AsyncClient

import src.app.dependencies as dependencies
from src.domain.dataset_workflow_ids import dataset_workflow_id_from_string
from src.domain.review_overrides import (
    InvalidReviewOverridesError,
    ReviewOverrideAction,
    ReviewOverrides,
    ReviewProgressState,
)
from src.persistence.review_override_store import (
    ReviewOverridesUnreadableError,
    load_review_overrides,
    save_review_overrides_state,
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
        "schema_version": 3,
        "file_id": file_id,
        "created_at": "2026-08-06T12:00:00+00:00",
        "updated_at": "2026-08-06T12:01:00+00:00",
        "events": [{
            "kind": "set",
            "row_key": "1",
            "column_key": "col_0000",
            "original_value": "Source",
            "selected_value": "Reviewed",
            "timestamp": "2026-08-06T12:00:30+00:00",
        }],
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


def _legacy_v2_review_state(file_id: str) -> dict[str, object]:
    payload = _valid_stored_review_state(file_id)
    payload["schema_version"] = 2
    payload["overrides"] = {
        "1": {
            "col_0000": {
                "human_value": "Reviewed",
                "original_value": "Source",
            },
        },
    }
    payload.pop("events")
    return payload


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
    payload["events"] = [{"kind": "set"}]


def _corrupt_progress(payload: dict[str, object]) -> None:
    progress = payload["review_state"]
    assert isinstance(progress, dict)
    payload["review_state"] = {
        **progress,
        "column_mode": {"current_unit": 0, "batch_size": 4},
    }


def _corrupt_event_order(payload: dict[str, object]) -> None:
    events = payload["events"]
    assert isinstance(events, list)
    later = deepcopy(events[0])
    earlier = deepcopy(events[0])
    assert isinstance(later, dict)
    assert isinstance(earlier, dict)
    later["timestamp"] = "2026-08-06T12:00:45+00:00"
    earlier["timestamp"] = "2026-08-06T12:00:15+00:00"
    payload["events"] = [later, earlier]


def _corrupt_clear_without_choice(payload: dict[str, object]) -> None:
    events = payload["events"]
    assert isinstance(events, list)
    clear = deepcopy(events[0])
    assert isinstance(clear, dict)
    clear["kind"] = "clear"
    clear["selected_value"] = None
    payload["events"] = [clear]


def _corrupt_changed_original(payload: dict[str, object]) -> None:
    events = payload["events"]
    assert isinstance(events, list)
    changed = deepcopy(events[0])
    assert isinstance(changed, dict)
    changed["original_value"] = "Different source"
    changed["selected_value"] = "Second review"
    changed["timestamp"] = "2026-08-06T12:00:45+00:00"
    events.append(changed)


def _corrupt_repeated_choice(payload: dict[str, object]) -> None:
    events = payload["events"]
    assert isinstance(events, list)
    repeated = deepcopy(events[0])
    assert isinstance(repeated, dict)
    repeated["timestamp"] = "2026-08-06T12:00:45+00:00"
    events.append(repeated)


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param(_corrupt_file_identity, id="file-identity"),
        pytest.param(_corrupt_timestamp, id="required-timestamp"),
        pytest.param(_corrupt_cell, id="complete-cell"),
        pytest.param(_corrupt_progress, id="review-progress"),
        pytest.param(_corrupt_event_order, id="chronological-events"),
        pytest.param(_corrupt_clear_without_choice, id="clear-without-choice"),
        pytest.param(_corrupt_changed_original, id="changed-original"),
        pytest.param(_corrupt_repeated_choice, id="repeated-choice"),
    ],
)
def test_store_rejects_corrupt_review_state_as_one_document(
    tmp_path: Path,
    corrupt: Callable[[dict[str, object]], None],
) -> None:
    """A corrupt field cannot silently remove edits or reset progress."""
    # Given: one stored v3 document with one corrupt field.
    payload = deepcopy(_valid_stored_review_state())
    corrupt(payload)
    storage, user = _stored_review_state(tmp_path, payload)

    # When/Then: the whole document is rejected.
    with pytest.raises(ReviewOverridesUnreadableError, match="review override"):
        load_review_overrides(storage, user, _FILE_ID)


def test_review_store_appends_only_meaningful_set_and_clear_events(tmp_path: Path) -> None:
    """The active snapshot is derived from decisions without duplicate autosave events."""
    # Given: a workflow with no saved review decisions.
    storage, user = _stored_review_state(tmp_path, _valid_stored_review_state())
    storage.delete_json(user, _FILE_ID, WorkflowFile.REVIEW_OVERRIDES)
    progress = ReviewProgressState()

    # When: a choice is saved, repeated, changed, and then removed.
    first = save_review_overrides_state(
        storage,
        user,
        file_id=_FILE_ID,
        overrides={
            "1": {"col_0000": {"human_value": "gamma", "original_value": "alpha"}},
        },
        review_state=progress,
    )
    repeated = save_review_overrides_state(
        storage,
        user,
        file_id=_FILE_ID,
        overrides={
            "1": {"col_0000": {"human_value": "gamma", "original_value": "alpha"}},
        },
        review_state=progress,
        expected_version=first.version,
    )
    changed = save_review_overrides_state(
        storage,
        user,
        file_id=_FILE_ID,
        overrides={
            "1": {"col_0000": {"human_value": "delta", "original_value": "alpha"}},
        },
        review_state=progress,
        expected_version=repeated.version,
    )
    cleared = save_review_overrides_state(
        storage,
        user,
        file_id=_FILE_ID,
        overrides={},
        review_state=progress,
        expected_version=changed.version,
    )

    # Then: the repeated save adds nothing, and replay ends with no active choice.
    assert len(first.value.events) == 1
    assert len(repeated.value.events) == 1
    assert [event.kind for event in cleared.value.events] == [
        ReviewOverrideAction.SET,
        ReviewOverrideAction.SET,
        ReviewOverrideAction.CLEAR,
    ]
    assert [event.selected_value for event in cleared.value.events] == ["gamma", "delta", None]
    assert cleared.value.overrides == {}


def test_active_review_snapshot_cannot_diverge_from_its_event_log(tmp_path: Path) -> None:
    """Callers cannot mutate the cached projection of stored reviewer decisions."""
    storage, user = _stored_review_state(tmp_path, _valid_stored_review_state())

    overrides = load_review_overrides(storage, user, _FILE_ID)

    assert overrides is not None
    assert isinstance(overrides.overrides, MappingProxyType)
    assert isinstance(overrides.overrides["1"], MappingProxyType)


def test_transition_rejects_a_changed_source_value() -> None:
    """A new snapshot cannot change the immutable source identity of one cell."""
    overrides = ReviewOverrides.from_store(_valid_stored_review_state(), _FILE_ID)

    with pytest.raises(InvalidReviewOverridesError, match="original values"):
        overrides.transition_to_snapshot(
            overrides={
                "1": {
                    "col_0000": {
                        "human_value": "Second review",
                        "original_value": "Different source",
                    }
                }
            },
            review_state=ReviewProgressState(),
            updated_at=datetime.fromisoformat("2026-08-06T12:02:00+00:00"),
        )


def test_transition_rejects_an_earlier_update_timestamp() -> None:
    """A clock rollback cannot create an event log that fails on its next read."""
    overrides = ReviewOverrides.from_store(_valid_stored_review_state(), _FILE_ID)

    with pytest.raises(InvalidReviewOverridesError, match="chronological"):
        overrides.transition_to_snapshot(
            overrides={},
            review_state=ReviewProgressState(),
            updated_at=datetime.fromisoformat("2026-08-06T12:00:59+00:00"),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/stage-4/rows",
        "/stage-5/summary",
        "/stage-5/download",
    ],
)
async def test_stage4_and_stage5_report_corrupt_saved_review_state_with_recovery(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
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
    # Given: a valid legacy v2 record for an otherwise ready workflow.
    storage.write_json(
        user,
        file_id,
        WorkflowFile.REVIEW_OVERRIDES,
        _legacy_v2_review_state(file_id),
    )

    # When: a Stage 4 or Stage 5 route reads review state.
    response = await app_client.post(path, json={"file_id": file_id})

    # Then: it gives one clear restart instruction.
    assert response.status_code == 409
    assert response.json() == {
        "detail": "The saved review state cannot be read. Return to Stage 3 and run harmonization again.",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/stage-4/rows",
        "/stage-5/summary",
        "/stage-5/download",
    ],
)
async def test_later_stages_reject_review_history_from_another_manifest(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
    path: str,
) -> None:
    """A stale review event cannot change output while disappearing from history."""
    file_id = await upload_content(
        app_client,
        create_csv_content([["source"], ["Current source"]]),
        "stale-review-state.csv",
    )
    meta = temp_storage.load(file_id)
    assert meta is not None
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, {})
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, {})
    stale = _valid_stored_review_state(file_id)
    events = stale["events"]
    assert isinstance(events, list)
    event = events[0]
    assert isinstance(event, dict)
    event["original_value"] = "Old source"
    dependencies.get_workflow_storage().write_json(
        dependencies.get_user_context(),
        file_id,
        WorkflowFile.REVIEW_OVERRIDES,
        stale,
    )

    response = await app_client.post(path, json={"file_id": file_id})

    assert response.status_code == 409
    assert response.json() == {
        "detail": "The saved review state cannot be read. Return to Stage 3 and run harmonization again.",
    }


@pytest.mark.asyncio
async def test_stage4_rows_reject_a_redundant_baseline_set_event(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """The stored snapshot follows the same baseline-is-clear rule as the save API."""
    file_id = await upload_content(
        app_client,
        create_csv_content([["source"], ["Current source"]]),
        "baseline-set-state.csv",
    )
    meta = temp_storage.load(file_id)
    assert meta is not None
    changes = {0: {"source": "AI"}}
    create_harmonized_csv(temp_storage, file_id, meta.saved_path, changes)
    create_manifest_for_file(temp_storage, file_id, meta.saved_path, changes)
    redundant = _valid_stored_review_state(file_id)
    events = redundant["events"]
    assert isinstance(events, list)
    event = events[0]
    assert isinstance(event, dict)
    event["original_value"] = "Current source"
    event["selected_value"] = "AI"
    dependencies.get_workflow_storage().write_json(
        dependencies.get_user_context(),
        file_id,
        WorkflowFile.REVIEW_OVERRIDES,
        redundant,
    )

    response = await app_client.post("/stage-4/rows", json={"file_id": file_id})

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
    # Given: a valid legacy v2 record and its current version.
    corrupt_payload = _legacy_v2_review_state(file_id)
    stored = storage.write_json(
        user,
        file_id,
        WorkflowFile.REVIEW_OVERRIDES,
        corrupt_payload,
    )
    encoded_version = base64.urlsafe_b64encode(stored.version.value.encode()).decode()

    # When: Stage 4 tries to replace it through autosave.
    response = await app_client.post(
        "/stage-4/overrides",
        headers={"If-Match": f'"{encoded_version}"'},
        json={
            "file_id": file_id,
            "overrides": {},
            "review_state": {},
        },
    )

    # Then: the save stops and leaves the v2 record unchanged.
    assert response.status_code == 409
    assert response.json() == {
        "detail": "The saved review state cannot be read. Return to Stage 3 and run harmonization again.",
    }
    unchanged = storage.read_json(user, file_id, WorkflowFile.REVIEW_OVERRIDES)
    assert unchanged is not None
    assert unchanged.data == corrupt_payload
    assert unchanged.version == stored.version
