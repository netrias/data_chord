"""Feature tests for restoring local scratch files from workflow storage."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from src.domain.dataset_workflow_ids import DatasetWorkflowId, dataset_workflow_id_from_string
from src.domain.manifest import normalize_manifest
from src.domain.storage import LocalWorkflowStorage, UploadConstraints, UploadStorage, UserContext, WorkflowFile
from src.domain.workflow_artifact_store import (
    load_harmonization_manifest_path,
    load_harmonized_output_path,
    load_mapping_manifest,
    load_upload_artifact,
    save_harmonized_artifacts,
    save_mapping_manifest,
    save_upload_artifacts,
)

pytestmark = pytest.mark.asyncio


def dataset_workflow_id(raw: str = "a" * 32) -> DatasetWorkflowId:
    return dataset_workflow_id_from_string(raw)


class InMemoryUpload:
    def __init__(self, content: bytes, filename: str = "dataset.csv") -> None:
        self.filename: str | None = filename
        self.content_type: str | None = "text/csv"
        self._content = content
        self._offset = 0

    async def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._content):
            return b""
        end = len(self._content) if size < 0 else self._offset + size
        chunk = self._content[self._offset:end]
        self._offset += len(chunk)
        return chunk

    async def close(self) -> None:
        return None


async def test_upload_artifact_restores_original_file_into_new_scratch_space(tmp_path: Path) -> None:
    # Given: an upload saved to workflow storage, with a separate empty local scratch space
    user = UserContext(user_id="alice")
    workflow_storage = LocalWorkflowStorage(tmp_path / "workflow")
    first_scratch = UploadStorage(tmp_path / "first", UploadConstraints(max_bytes=10_000))
    second_scratch = UploadStorage(tmp_path / "second", UploadConstraints(max_bytes=10_000))
    content = b"diagnosis\nalpha\n"
    meta = await first_scratch.store(InMemoryUpload(content), dataset_workflow_id())
    workflow_storage.create_workflow(user, meta.dataset_workflow_id)
    save_upload_artifacts(workflow_storage, user, first_scratch, meta)

    assert second_scratch.load(meta.file_id) is None

    # When: a later request lands on scratch space that has no local copy
    restored = load_upload_artifact(second_scratch, workflow_storage, user, meta.file_id)

    # Then: the original file and metadata are restored locally
    assert restored is not None
    assert restored.file_id == meta.file_id
    assert restored.saved_path.read_bytes() == content
    assert second_scratch.load(meta.file_id) is not None


async def test_upload_artifact_refreshes_local_metadata_from_workflow_storage(tmp_path: Path) -> None:
    # Given: local scratch has the file, but workflow storage has newer metadata
    user = UserContext(user_id="alice")
    workflow_storage = LocalWorkflowStorage(tmp_path / "workflow")
    scratch = UploadStorage(tmp_path / "scratch", UploadConstraints(max_bytes=10_000))
    meta = await scratch.store(InMemoryUpload(b"diagnosis\nalpha\n", filename="first.csv"), dataset_workflow_id())
    workflow_storage.create_workflow(user, meta.dataset_workflow_id)
    save_upload_artifacts(workflow_storage, user, scratch, meta)
    stored = workflow_storage.read_json(user, meta.file_id, WorkflowFile.UPLOAD_METADATA)
    assert stored is not None
    assert isinstance(stored.data, Mapping)
    local_meta = scratch.load(meta.file_id)
    assert local_meta is not None
    assert local_meta.original_name == "first.csv"

    updated_metadata = dict(stored.data)
    updated_metadata["original_name"] = "renamed.csv"
    workflow_storage.write_json(
        user,
        meta.file_id,
        WorkflowFile.UPLOAD_METADATA,
        updated_metadata,
        expected_version=stored.version,
    )

    # When: the upload is loaded while the local file already exists
    restored = load_upload_artifact(scratch, workflow_storage, user, meta.file_id)

    # Then: local metadata is refreshed from durable storage
    assert restored is not None
    assert restored.original_name == "renamed.csv"


async def test_generated_artifacts_restore_into_new_scratch_space(tmp_path: Path) -> None:
    # Given: Stage 3 artifacts saved durably after being generated in one scratch space
    user = UserContext(user_id="alice")
    workflow_storage = LocalWorkflowStorage(tmp_path / "workflow")
    first_scratch = UploadStorage(tmp_path / "first", UploadConstraints(max_bytes=10_000))
    second_scratch = UploadStorage(tmp_path / "second", UploadConstraints(max_bytes=10_000))
    meta = await first_scratch.store(InMemoryUpload(b"diagnosis\nalpha\n"), dataset_workflow_id())
    workflow_storage.create_workflow(user, meta.dataset_workflow_id)
    save_upload_artifacts(workflow_storage, user, first_scratch, meta)

    manifest = {
        "column_mappings": {
            "col_0000": {
                "cde_key": "primary_diagnosis",
                "cde_id": 1,
                "column_name": "diagnosis",
            }
        }
    }
    save_mapping_manifest(workflow_storage, user, meta.file_id, manifest)
    harmonized_path = first_scratch.harmonized_path_for(meta.file_id, meta.saved_path)
    harmonized_path.write_bytes(b"diagnosis\nbeta\n")
    manifest_path = tmp_path / "manifest.parquet"
    manifest_path.write_bytes(b"fake parquet bytes")
    save_harmonized_artifacts(workflow_storage, user, meta.file_id, harmonized_path, manifest_path)

    assert second_scratch.load_manifest(meta.file_id) is None
    assert second_scratch.load_harmonized_path(meta.file_id) is None
    assert second_scratch.load_harmonization_manifest_path(meta.file_id) is None

    # When: the next request restores all durable artifacts into a new scratch space
    restored_meta = load_upload_artifact(second_scratch, workflow_storage, user, meta.file_id)
    restored_mapping = load_mapping_manifest(second_scratch, workflow_storage, user, meta.file_id)
    assert restored_meta is not None
    restored_output = load_harmonized_output_path(
        second_scratch,
        workflow_storage,
        user,
        meta.file_id,
        restored_meta,
    )
    restored_manifest = load_harmonization_manifest_path(second_scratch, workflow_storage, user, meta.file_id)

    # Then: local readers can use normal paths again
    assert restored_mapping == normalize_manifest(manifest)
    assert restored_output is not None
    assert restored_output.read_bytes() == b"diagnosis\nbeta\n"
    assert restored_manifest is not None
    assert restored_manifest.read_bytes() == b"fake parquet bytes"
