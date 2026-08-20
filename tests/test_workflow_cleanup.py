"""Portable workflow capacity cleanup behavior."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Never

import pytest

from src.domain.dataset_workflow_ids import DatasetWorkflowId, dataset_workflow_id_from_string
from src.storage import LocalWorkflowStorage, UploadConstraints, UploadStorage, UserContext, WorkflowFile
from src.storage.workflow_cleanup import WorkflowCleanup, WorkflowStorageFullError


def _workflow_id(character: str) -> DatasetWorkflowId:
    return dataset_workflow_id_from_string(character * 32)


def _create_workflow(
    storage: LocalWorkflowStorage,
    user: UserContext,
    root: Path,
    workflow_id: DatasetWorkflowId,
    last_accessed_at: datetime,
) -> Path:
    workflow = storage.create_workflow(user, workflow_id)
    source = root / f"{workflow_id}.csv"
    source.write_bytes(b"value\n" + b"x" * 512)
    storage.create_artifact(user, workflow.dataset_workflow_id, WorkflowFile.ORIGINAL_UPLOAD, source)
    workflow_dir = root / "data" / "workflows" / workflow.dataset_workflow_id
    metadata_path = workflow_dir / "metadata.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    payload["created_at"] = (last_accessed_at - timedelta(hours=1)).isoformat()
    payload["last_accessed_at"] = last_accessed_at.isoformat()
    metadata_path.write_text(json.dumps(payload), encoding="utf-8")
    return workflow_dir


def test_cleanup_deletes_least_recently_accessed_workflow_after_upload(tmp_path: Path) -> None:
    # Given: workflow storage is over its capacity threshold with two old workflows and one recent workflow.
    now = datetime.now(UTC)
    data_dir = tmp_path / "data"
    scratch_dir = tmp_path / "scratch"
    storage = LocalWorkflowStorage(data_dir)
    uploads = UploadStorage(scratch_dir, UploadConstraints(max_bytes=1024))
    user = UserContext(user_id="local-user")
    oldest = _create_workflow(storage, user, tmp_path, _workflow_id("a"), now - timedelta(days=4))
    older = _create_workflow(storage, user, tmp_path, _workflow_id("b"), now - timedelta(days=3))
    recent = _create_workflow(storage, user, tmp_path, _workflow_id("c"), now - timedelta(hours=1))
    standards = data_dir / "standards.sqlite"
    standards.write_bytes(b"standards")
    scratch_paths = (
        scratch_dir / "files" / f"{_workflow_id('a')}.csv",
        scratch_dir / "meta" / f"{_workflow_id('a')}.json",
        scratch_dir / "manifests" / f"{_workflow_id('a')}_harmonization.parquet",
        scratch_dir / "harmonized" / f"{_workflow_id('a')}.harmonized.csv",
    )
    for scratch_path in scratch_paths:
        scratch_path.write_bytes(b"scratch")
    capacity_bytes = storage.workflow_inventory().usage_bytes

    assert oldest.exists()
    assert older.exists()
    assert recent.exists()

    # When: background cleanup enforces the configured capacity.
    result = WorkflowCleanup(storage, uploads, capacity_bytes=capacity_bytes).run(now=now)

    # Then: it deletes the least recently accessed eligible workflow and preserves recent work and standards.
    assert result.deleted_workflow_ids == (_workflow_id("a"),)
    assert oldest.exists() is False
    assert older.exists()
    assert recent.exists()
    assert standards.read_bytes() == b"standards"
    assert all(path.exists() is False for path in scratch_paths)
    assert result.usage_bytes_after <= result.target_bytes


def test_cleanup_skips_workflow_with_invalid_metadata(tmp_path: Path) -> None:
    # Given: storage is over its threshold but one workflow has unreadable metadata.
    now = datetime.now(UTC)
    data_dir = tmp_path / "data"
    storage = LocalWorkflowStorage(data_dir)
    uploads = UploadStorage(tmp_path / "scratch", UploadConstraints(max_bytes=1024))
    invalid_dir = data_dir / "workflows" / _workflow_id("d")
    invalid_dir.mkdir(parents=True)
    (invalid_dir / "metadata.json").write_text("not-json", encoding="utf-8")
    (invalid_dir / "large.bin").write_bytes(b"x" * 1024)

    assert invalid_dir.exists()

    # When: cleanup scans the workflow directory.
    result = WorkflowCleanup(storage, uploads, capacity_bytes=1024).run(now=now)

    # Then: invalid state counts toward usage but is not deleted automatically.
    assert invalid_dir.exists()
    assert result.deleted_workflow_ids == ()
    assert result.usage_bytes_after > result.target_bytes


def test_cleanup_rechecks_access_time_before_deletion(tmp_path: Path) -> None:
    # Given: cleanup selected an old workflow before a user accessed it again.
    now = datetime.now(UTC)
    data_dir = tmp_path / "data"
    storage = LocalWorkflowStorage(data_dir)
    user = UserContext(user_id="local-user")
    workflow_id = _workflow_id("e")
    workflow_dir = _create_workflow(storage, user, tmp_path, workflow_id, now - timedelta(days=2))
    selected_access_time = storage.workflow_inventory().workflows[0].metadata.last_accessed_at
    storage.read_json(user, workflow_id, WorkflowFile.WORKFLOW_STATE)

    # When: cleanup tries to delete using its stale selection.
    deleted = storage.delete_workflow_if_last_accessed(
        workflow_id,
        selected_access_time,
        lambda _file_id: None,
    )

    # Then: the refreshed workflow is retained.
    assert deleted is False
    assert workflow_dir.exists()


def test_only_one_cleanup_operation_holds_the_volume_lease(tmp_path: Path) -> None:
    # Given: one cleanup operation holds the portable volume lease.
    storage = LocalWorkflowStorage(tmp_path / "data")

    with storage.try_cleanup_lease() as first_acquired:
        assert first_acquired is True

        # When: a second cleanup operation starts for the same volume.
        with storage.try_cleanup_lease() as second_acquired:
            # Then: it exits without running concurrently.
            assert second_acquired is False


@pytest.mark.parametrize(
    "invalid_update",
    [
        {"file_id": "not-a-workflow-id"},
        {"last_accessed_at": "not-a-timestamp"},
        {"last_accessed_at": None},
    ],
)
def test_cleanup_skips_valid_json_with_invalid_metadata_fields(
    tmp_path: Path,
    invalid_update: dict[str, object],
) -> None:
    # Given: one workflow has valid JSON with an invalid identity or access timestamp.
    now = datetime.now(UTC)
    data_dir = tmp_path / "data"
    storage = LocalWorkflowStorage(data_dir)
    uploads = UploadStorage(tmp_path / "scratch", UploadConstraints(max_bytes=1024))
    user = UserContext(user_id="local-user")
    invalid_dir = _create_workflow(storage, user, tmp_path, _workflow_id("f"), now - timedelta(days=3))
    metadata_path = invalid_dir / "metadata.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    payload.update(invalid_update)
    metadata_path.write_text(json.dumps(payload), encoding="utf-8")

    # When: cleanup inventories the volume.
    result = WorkflowCleanup(
        storage,
        uploads,
        capacity_bytes=storage.workflow_inventory().usage_bytes,
    ).run(now=now)

    # Then: the malformed workflow counts toward capacity but is not deleted.
    assert invalid_dir.exists()
    assert result.deleted_workflow_ids == ()


def test_scratch_deletion_failure_keeps_durable_workflow_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an old workflow is eligible, but its scratch filesystem rejects deletion once.
    now = datetime.now(UTC)
    data_dir = tmp_path / "data"
    storage = LocalWorkflowStorage(data_dir)
    uploads = UploadStorage(tmp_path / "scratch", UploadConstraints(max_bytes=1024))
    user = UserContext(user_id="local-user")
    workflow_id = _workflow_id("9")
    workflow_dir = _create_workflow(storage, user, tmp_path, workflow_id, now - timedelta(days=3))
    cleanup = WorkflowCleanup(
        storage,
        uploads,
        capacity_bytes=storage.workflow_inventory().usage_bytes,
    )
    original_delete = uploads.delete_workflow_files

    def _fail_delete(_file_id: DatasetWorkflowId) -> None:
        raise OSError("scratch unavailable")

    monkeypatch.setattr(uploads, "delete_workflow_files", _fail_delete)

    # When: cleanup tries to remove the workflow.
    first_result = cleanup.run(now=now)

    # Then: durable data remains eligible, and a later cleanup can finish the deletion.
    assert first_result.deleted_workflow_ids == ()
    assert workflow_dir.exists()
    monkeypatch.setattr(uploads, "delete_workflow_files", original_delete)
    second_result = cleanup.run(now=now)
    assert second_result.deleted_workflow_ids == (workflow_id,)
    assert workflow_dir.exists() is False


def test_low_free_space_runs_cleanup_before_rejecting_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: real free space is low, but one old workflow can be removed.
    now = datetime.now(UTC)
    data_dir = tmp_path / "data"
    storage = LocalWorkflowStorage(data_dir)
    uploads = UploadStorage(tmp_path / "scratch", UploadConstraints(max_bytes=1024))
    user = UserContext(user_id="local-user")
    workflow_id = _workflow_id("8")
    workflow_dir = _create_workflow(storage, user, tmp_path, workflow_id, now - timedelta(days=3))
    cleanup = WorkflowCleanup(
        storage,
        uploads,
        capacity_bytes=storage.workflow_inventory().usage_bytes,
        required_free_bytes=100,
    )
    monkeypatch.setattr(storage, "available_bytes", lambda: 0 if workflow_dir.exists() else 200)
    monkeypatch.setattr(uploads, "available_bytes", lambda: 200)

    # When: the upload guard checks its write margin.
    cleanup.require_upload_space()

    # Then: emergency cleanup creates enough margin instead of rejecting the upload.
    assert workflow_dir.exists() is False


def test_upload_lease_creation_error_reports_storage_full(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a portable volume cannot create its upload lock file.
    storage = LocalWorkflowStorage(tmp_path / "data")
    uploads = UploadStorage(tmp_path / "scratch", UploadConstraints(max_bytes=1024))
    cleanup = WorkflowCleanup(storage, uploads, capacity_bytes=1024)

    def _fail_lease() -> Never:
        raise OSError("no inode available")

    monkeypatch.setattr(storage, "acquire_upload_lease", _fail_lease)

    # When / Then: the upload reservation reports the operator-facing storage condition.
    with pytest.raises(WorkflowStorageFullError, match="cannot reserve"):
        cleanup.acquire_upload_lease()
