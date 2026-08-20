"""Contract tests for typed workflow storage."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.domain.dataset_workflow_ids import DatasetWorkflowId, dataset_workflow_id_from_string
from src.storage import (
    LocalWorkflowStorage,
    UserContext,
    WorkflowAccessDeniedError,
    WorkflowArtifactNotFoundError,
    WorkflowArtifactSuffixError,
    WorkflowArtifactTypeError,
    WorkflowConflictError,
    WorkflowFile,
)


def dataset_workflow_id(raw: str = "a" * 32) -> DatasetWorkflowId:
    return dataset_workflow_id_from_string(raw)


def test_workflow_storage_dependency_uses_local_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: local workflow storage is selected through environment config
    import src.app.dependencies as dependencies

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


def test_local_workflow_access_refreshes_last_accessed_time(tmp_path: Path) -> None:
    # Given: a local workflow whose stored access time is old.
    storage = LocalWorkflowStorage(tmp_path / "storage")
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    metadata_path = tmp_path / "storage" / "workflows" / workflow.dataset_workflow_id / "metadata.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    old_access = datetime.now(UTC) - timedelta(days=2)
    payload["created_at"] = (old_access - timedelta(hours=1)).isoformat()
    payload["last_accessed_at"] = old_access.isoformat()
    metadata_path.write_text(json.dumps(payload), encoding="utf-8")

    # When: the workflow is read through the local storage client.
    storage.read_json(user, workflow.dataset_workflow_id, WorkflowFile.WORKFLOW_STATE)

    # Then: the storage client records a newer access time without changing the caller contract.
    refreshed = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert datetime.fromisoformat(refreshed["last_accessed_at"]) > old_access


def test_local_workflow_inventory_accepts_metadata_written_before_access_tracking(tmp_path: Path) -> None:
    # Given: workflow metadata from the earlier schema has only its creation time.
    storage = LocalWorkflowStorage(tmp_path / "storage")
    workflow = storage.create_workflow(UserContext(user_id="alice"), dataset_workflow_id())
    metadata_path = tmp_path / "storage" / "workflows" / workflow.dataset_workflow_id / "metadata.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    created_at = payload["created_at"]
    del payload["last_accessed_at"]
    metadata_path.write_text(json.dumps(payload), encoding="utf-8")

    # When: the local client inventories workflows for cleanup.
    inventory = storage.workflow_inventory()

    # Then: creation time is the compatible last-access fallback.
    assert len(inventory.workflows) == 1
    assert inventory.workflows[0].metadata.last_accessed_at.isoformat() == created_at


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


def test_create_once_artifact_rejects_second_suffix(tmp_path: Path) -> None:
    storage = LocalWorkflowStorage(tmp_path / "storage")
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    csv_source = tmp_path / "sample.csv"
    tsv_source = tmp_path / "sample.tsv"
    csv_source.write_text("a\nold\n", encoding="utf-8")
    tsv_source.write_text("a\tnew\n", encoding="utf-8")
    storage.create_artifact(user, workflow.dataset_workflow_id, WorkflowFile.ORIGINAL_UPLOAD, csv_source)

    with pytest.raises(WorkflowConflictError, match="already exists"):
        storage.create_artifact(user, workflow.dataset_workflow_id, WorkflowFile.ORIGINAL_UPLOAD, tsv_source)

    with storage.materialize_artifact(user, workflow.dataset_workflow_id, WorkflowFile.ORIGINAL_UPLOAD) as path:
        assert path.suffix == ".csv"
        assert path.read_text(encoding="utf-8") == "a\nold\n"


def test_create_once_artifact_preserves_the_winner_of_a_publish_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = LocalWorkflowStorage(tmp_path / "storage")
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    source = tmp_path / "candidate.csv"
    source.write_text("a\ncandidate\n", encoding="utf-8")
    artifact_dir = tmp_path / "storage" / "workflows" / workflow.dataset_workflow_id / "artifacts"
    artifact_dir.mkdir()
    winner = artifact_dir / "original_upload.csv"
    winner.write_text("a\nwinner\n", encoding="utf-8")
    monkeypatch.setattr(storage, "_existing_artifact_paths", lambda _file_id, _kind: [])

    with pytest.raises(WorkflowConflictError, match="already exists"):
        storage.create_artifact(user, workflow.dataset_workflow_id, WorkflowFile.ORIGINAL_UPLOAD, source)

    assert winner.read_text(encoding="utf-8") == "a\nwinner\n"


def test_original_upload_cannot_be_replaced_through_write_artifact(tmp_path: Path) -> None:
    storage = LocalWorkflowStorage(tmp_path / "storage")
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    original = tmp_path / "original.csv"
    replacement = tmp_path / "replacement.csv"
    original.write_text("a\nold\n", encoding="utf-8")
    replacement.write_text("a\nnew\n", encoding="utf-8")
    storage.create_artifact(user, workflow.dataset_workflow_id, WorkflowFile.ORIGINAL_UPLOAD, original)

    with pytest.raises(WorkflowConflictError, match="create-once"):
        storage.write_artifact(user, workflow.dataset_workflow_id, WorkflowFile.ORIGINAL_UPLOAD, replacement)

    with storage.materialize_artifact(user, workflow.dataset_workflow_id, WorkflowFile.ORIGINAL_UPLOAD) as path:
        assert path.read_text(encoding="utf-8") == "a\nold\n"


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


def test_mutable_artifact_replaces_only_the_same_suffix(tmp_path: Path) -> None:
    storage = LocalWorkflowStorage(tmp_path / "storage")
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    different = tmp_path / "different.tsv"
    first.write_text("a\nold\n", encoding="utf-8")
    second.write_text("a\nnew\n", encoding="utf-8")
    different.write_text("a\trejected\n", encoding="utf-8")

    storage.write_artifact(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT, first)
    storage.write_artifact(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT, second)
    with pytest.raises(WorkflowConflictError, match="suffix"):
        storage.write_artifact(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT, different)

    artifact_dir = tmp_path / "storage" / "workflows" / workflow.dataset_workflow_id / "artifacts"
    assert [path.name for path in artifact_dir.iterdir()] == ["harmonized_output.csv"]
    with storage.materialize_artifact(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT) as path:
        assert path.read_text(encoding="utf-8") == "a\nnew\n"


def test_artifact_lookup_matches_the_exact_logical_name(tmp_path: Path) -> None:
    storage = LocalWorkflowStorage(tmp_path / "storage")
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    source = tmp_path / "output.csv"
    source.write_text("a\ncurrent\n", encoding="utf-8")
    storage.write_artifact(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT, source)
    artifact_dir = tmp_path / "storage" / "workflows" / workflow.dataset_workflow_id / "artifacts"
    (artifact_dir / "harmonized_output_backup.csv").write_text("a\nstale\n", encoding="utf-8")

    with storage.materialize_artifact(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT) as path:
        assert path.name == "harmonized_output.csv"
        assert path.read_text(encoding="utf-8") == "a\ncurrent\n"


def test_artifact_rejects_missing_source_and_missing_suffix(tmp_path: Path) -> None:
    storage = LocalWorkflowStorage(tmp_path / "storage")
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    no_suffix = tmp_path / "artifact"
    no_suffix.write_text("content", encoding="utf-8")

    with pytest.raises(WorkflowArtifactSuffixError, match="suffix"):
        storage.write_artifact(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT, no_suffix)
    with pytest.raises(WorkflowArtifactNotFoundError, match="Source artifact not found"):
        storage.write_artifact(
            user,
            workflow.dataset_workflow_id,
            WorkflowFile.HARMONIZED_OUTPUT,
            tmp_path / "missing.csv",
        )


def test_artifact_lookup_rejects_multiple_exact_variants_and_recovers(tmp_path: Path) -> None:
    storage = LocalWorkflowStorage(tmp_path / "storage")
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    source = tmp_path / "output.csv"
    source.write_text("a\ncurrent\n", encoding="utf-8")
    storage.write_artifact(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT, source)
    artifact_dir = tmp_path / "storage" / "workflows" / workflow.dataset_workflow_id / "artifacts"
    stale_variant = artifact_dir / "harmonized_output.tsv"
    stale_variant.write_text("a\tstale\n", encoding="utf-8")

    with pytest.raises(WorkflowConflictError, match="multiple"):
        with storage.materialize_artifact(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT):
            pass

    stale_variant.unlink()
    with storage.materialize_artifact(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT) as path:
        assert path.read_text(encoding="utf-8") == "a\ncurrent\n"


def test_artifact_lookup_reports_zero_variants(tmp_path: Path) -> None:
    storage = LocalWorkflowStorage(tmp_path / "storage")
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())

    with pytest.raises(WorkflowArtifactNotFoundError, match="not found"):
        with storage.materialize_artifact(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT):
            pass


def test_failed_local_artifact_replacement_preserves_last_complete_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = LocalWorkflowStorage(tmp_path / "storage")
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    previous = tmp_path / "previous.csv"
    replacement = tmp_path / "replacement.csv"
    previous.write_text("a\nold\n", encoding="utf-8")
    replacement.write_text("a\tnew\n", encoding="utf-8")
    storage.write_artifact(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT, previous)

    def _fail_copy(_source: Path, _destination: Path) -> None:
        raise OSError("simulated copy failure")

    monkeypatch.setattr(storage, "_copy_artifact_atomic", _fail_copy)
    with pytest.raises(OSError, match="simulated copy failure"):
        storage.write_artifact(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT, replacement)

    with storage.materialize_artifact(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT) as path:
        assert path.suffix == ".csv"
        assert path.read_text(encoding="utf-8") == "a\nold\n"

    monkeypatch.undo()
    storage.write_artifact(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT, replacement)
    with storage.materialize_artifact(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT) as path:
        assert path.read_text(encoding="utf-8") == "a\tnew\n"


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
