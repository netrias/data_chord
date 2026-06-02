"""Contract tests for typed workflow storage."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.domain.dataset_workflow_ids import DatasetWorkflowId, dataset_workflow_id_from_string
from src.domain.storage import (
    LocalWorkflowStorage,
    UserContext,
    WorkflowAccessDeniedError,
    WorkflowArtifactTypeError,
    WorkflowConflictError,
    WorkflowFile,
)


def dataset_workflow_id(raw: str = "a" * 32) -> DatasetWorkflowId:
    return dataset_workflow_id_from_string(raw)


def test_workflow_storage_dependency_uses_local_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: local workflow storage is selected through environment config
    import src.domain.dependencies as dependencies

    original_workflow_storage = dependencies._workflow_storage
    try:
        monkeypatch.setenv("DATA_CHORD_STORAGE", "local")
        monkeypatch.setenv("DATA_CHORD_WORKFLOW_STORAGE_DIR", str(tmp_path / "configured-storage"))
        dependencies._workflow_storage = None

        assert not (tmp_path / "configured-storage").exists()

        # When: the app asks for workflow storage
        storage = dependencies.get_workflow_storage()

        # Then: the configured local backend is initialized
        assert isinstance(storage, LocalWorkflowStorage)
        assert (tmp_path / "configured-storage" / "workflows").is_dir()
    finally:
        dependencies._workflow_storage = original_workflow_storage


def test_workflow_json_is_owned_by_creator(tmp_path: Path) -> None:
    # Given: Alice has created a workflow and Bob has no access to it
    storage = LocalWorkflowStorage(tmp_path / "storage")
    alice = UserContext(user_id="alice", email="alice@example.test")
    bob = UserContext(user_id="bob", email="bob@example.test")
    workflow = storage.create_workflow(alice, dataset_workflow_id())

    assert storage.read_json(alice, workflow.dataset_workflow_id, WorkflowFile.WORKFLOW_STATE) is None

    # When: Alice stores workflow state
    stored = storage.write_json(
        alice,
        workflow.dataset_workflow_id,
        WorkflowFile.WORKFLOW_STATE,
        {"stage": "uploaded"},
    )

    # Then: Alice can read it, but Bob cannot
    assert stored.data == {"stage": "uploaded"}
    read_back = storage.read_json(alice, workflow.dataset_workflow_id, WorkflowFile.WORKFLOW_STATE)
    assert read_back is not None
    assert read_back.data == {"stage": "uploaded"}
    with pytest.raises(WorkflowAccessDeniedError):
        storage.read_json(bob, workflow.dataset_workflow_id, WorkflowFile.WORKFLOW_STATE)


def test_admin_can_read_another_users_workflow(tmp_path: Path) -> None:
    # Given: Alice owns a workflow and an admin has not read it yet
    storage = LocalWorkflowStorage(tmp_path / "storage")
    alice = UserContext(user_id="alice")
    admin = UserContext(user_id="admin", is_admin=True)
    workflow = storage.create_workflow(alice, dataset_workflow_id())
    storage.write_json(alice, workflow.dataset_workflow_id, WorkflowFile.WORKFLOW_STATE, {"stage": "uploaded"})

    assert admin.user_id != workflow.owner_user_id

    # When: the admin reads the workflow state
    stored = storage.read_json(admin, workflow.dataset_workflow_id, WorkflowFile.WORKFLOW_STATE)

    # Then: ownership does not block admin access
    assert stored is not None
    assert stored.data == {"stage": "uploaded"}


def test_mutable_json_requires_latest_version(tmp_path: Path) -> None:
    # Given: a mutable workflow state has been read once
    storage = LocalWorkflowStorage(tmp_path / "storage")
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    first = storage.write_json(user, workflow.dataset_workflow_id, WorkflowFile.WORKFLOW_STATE, {"stage": "uploaded"})

    assert first.version.value

    # When: the state is updated with the latest version
    second = storage.write_json(
        user,
        workflow.dataset_workflow_id,
        WorkflowFile.WORKFLOW_STATE,
        {"stage": "mapped"},
        expected_version=first.version,
    )

    # Then: retrying with the stale version is rejected
    assert second.data == {"stage": "mapped"}
    with pytest.raises(WorkflowConflictError):
        storage.write_json(
            user,
            workflow.dataset_workflow_id,
            WorkflowFile.WORKFLOW_STATE,
            {"stage": "harmonized"},
            expected_version=first.version,
        )


def test_create_once_artifact_rejects_overwrite(tmp_path: Path) -> None:
    # Given: the original upload artifact has already been written
    storage = LocalWorkflowStorage(tmp_path / "storage")
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    first_source = tmp_path / "sample.csv"
    second_source = tmp_path / "other.csv"
    first_source.write_text("a,b\n1,2\n", encoding="utf-8")
    second_source.write_text("a,b\n3,4\n", encoding="utf-8")
    storage.create_artifact(user, workflow.dataset_workflow_id, WorkflowFile.ORIGINAL_UPLOAD, first_source)

    with storage.materialize_artifact(user, workflow.dataset_workflow_id, WorkflowFile.ORIGINAL_UPLOAD) as materialized:
        assert materialized.read_text(encoding="utf-8") == "a,b\n1,2\n"

    # When / Then: create-only storage rejects a second original upload
    with pytest.raises(WorkflowConflictError):
        storage.create_artifact(user, workflow.dataset_workflow_id, WorkflowFile.ORIGINAL_UPLOAD, second_source)


def test_mutable_json_can_be_deleted(tmp_path: Path) -> None:
    # Given: review overrides have been stored for a workflow
    storage = LocalWorkflowStorage(tmp_path / "storage")
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    storage.write_json(user, workflow.dataset_workflow_id, WorkflowFile.REVIEW_OVERRIDES, {"overrides": {}})

    assert storage.read_json(user, workflow.dataset_workflow_id, WorkflowFile.REVIEW_OVERRIDES) is not None

    # When: the mutable JSON artifact is deleted
    deleted = storage.delete_json(user, workflow.dataset_workflow_id, WorkflowFile.REVIEW_OVERRIDES)

    # Then: the first delete reports work done and the second is a no-op
    assert deleted is True
    assert storage.read_json(user, workflow.dataset_workflow_id, WorkflowFile.REVIEW_OVERRIDES) is None
    assert storage.delete_json(user, workflow.dataset_workflow_id, WorkflowFile.REVIEW_OVERRIDES) is False


def test_file_artifact_materializes_as_local_path(tmp_path: Path) -> None:
    # Given: a source CSV and an empty workflow
    storage = LocalWorkflowStorage(tmp_path / "storage")
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    source_path = tmp_path / "sample.csv"
    source_path.write_text("a,b\n1,2\n", encoding="utf-8")

    assert not (tmp_path / "storage" / "workflows" / workflow.dataset_workflow_id / "artifacts").exists()

    # When: the upload artifact is stored and materialized
    artifact = storage.create_artifact(user, workflow.dataset_workflow_id, WorkflowFile.ORIGINAL_UPLOAD, source_path)

    # Then: callers receive a real local path with the original suffix preserved
    assert artifact.suffix == ".csv"
    with storage.materialize_artifact(user, workflow.dataset_workflow_id, WorkflowFile.ORIGINAL_UPLOAD) as materialized:
        assert materialized.suffix == ".csv"
        assert materialized.read_text(encoding="utf-8") == "a,b\n1,2\n"


def test_json_and_file_artifact_operations_are_not_interchangeable(tmp_path: Path) -> None:
    # Given: a workflow with no artifacts
    storage = LocalWorkflowStorage(tmp_path / "storage")
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    source_path = tmp_path / "sample.csv"
    source_path.write_text("a,b\n1,2\n", encoding="utf-8")

    assert storage.read_json(user, workflow.dataset_workflow_id, WorkflowFile.WORKFLOW_STATE) is None

    # When / Then: JSON APIs reject file artifacts and file APIs reject JSON artifacts
    with pytest.raises(WorkflowArtifactTypeError):
        storage.read_json(user, workflow.dataset_workflow_id, WorkflowFile.ORIGINAL_UPLOAD)
    with pytest.raises(WorkflowArtifactTypeError):
        storage.create_artifact(user, workflow.dataset_workflow_id, WorkflowFile.WORKFLOW_STATE, source_path)
