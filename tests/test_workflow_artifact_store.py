"""Feature tests for restoring local scratch files from workflow storage."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from src.domain.dataset_workflow_ids import DatasetWorkflowId, dataset_workflow_id_from_string
from src.persistence.workflow_artifacts import (
    UploadMetadataUnreadableError,
    open_generated_artifact,
    open_upload_artifact,
    save_harmonized_artifacts,
    save_upload_artifacts,
)
from src.storage import (
    LocalWorkflowStorage,
    UploadConstraints,
    UploadStorage,
    UserContext,
    WorkflowAccessDeniedError,
    WorkflowFile,
    WorkflowNotFoundError,
)

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("load_manifest", [False, True], ids=["output", "manifest"])
@pytest.mark.parametrize("authorized", [False, True], ids=["other-user", "owner"])
async def test_generated_artifact_requires_access_before_missing_artifact_handling(
    tmp_path: Path,
    load_manifest: bool,
    authorized: bool,
) -> None:
    # Given: an owned upload with no generated artifact and a caller with or without access.
    owner = UserContext(user_id="alice")
    caller = owner if authorized else UserContext(user_id="bob")
    workflow_storage = LocalWorkflowStorage(tmp_path / "workflow")
    scratch = UploadStorage(tmp_path / "scratch", UploadConstraints(max_bytes=10_000))
    meta = await scratch.store(InMemoryUpload(b"diagnosis\nalpha\n"), dataset_workflow_id())
    workflow_storage.create_workflow(owner, meta.dataset_workflow_id)
    save_upload_artifacts(workflow_storage, owner, scratch, meta)

    kind = WorkflowFile.HARMONIZATION_MANIFEST_BASE if load_manifest else WorkflowFile.HARMONIZED_OUTPUT
    # When the caller requests the missing artifact.
    if authorized:
        with open_generated_artifact(workflow_storage, caller, meta.file_id, kind) as result:
            # Then the owner can observe its absence.
            assert result is None
    else:
        with (
            pytest.raises(WorkflowAccessDeniedError),
            open_generated_artifact(workflow_storage, caller, meta.file_id, kind),
        ):
            # Then the caller must have been denied before a path is exposed.
            pytest.fail("another user's artifact was accessible")


@pytest.mark.parametrize("load_manifest", [False, True], ids=["output", "manifest"])
async def test_generated_artifact_raises_when_workflow_metadata_is_missing(
    tmp_path: Path,
    load_manifest: bool,
) -> None:
    # Given: a local upload with no durable workflow metadata.
    user = UserContext(user_id="alice")
    workflow_storage = LocalWorkflowStorage(tmp_path / "workflow")
    scratch = UploadStorage(tmp_path / "scratch", UploadConstraints(max_bytes=10_000))
    meta = await scratch.store(InMemoryUpload(b"diagnosis\nalpha\n"), dataset_workflow_id())

    # When: a generated artifact is requested.
    # Then: missing workflow metadata is not treated as a missing artifact.
    kind = WorkflowFile.HARMONIZATION_MANIFEST_BASE if load_manifest else WorkflowFile.HARMONIZED_OUTPUT
    with pytest.raises(WorkflowNotFoundError), open_generated_artifact(workflow_storage, user, meta.file_id, kind):
        pytest.fail("an unowned artifact was accessible")


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
        chunk = self._content[self._offset : end]
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
    with open_upload_artifact(second_scratch, workflow_storage, user, meta.file_id) as restored:
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
    with open_upload_artifact(scratch, workflow_storage, user, meta.file_id) as restored:
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

    harmonized_path = first_scratch.harmonized_path_for(meta.file_id, meta.saved_path)
    harmonized_path.write_bytes(b"diagnosis\nbeta\n")
    manifest_path = tmp_path / "manifest.parquet"
    manifest_path.write_bytes(b"fake parquet bytes")
    save_harmonized_artifacts(workflow_storage, user, meta.file_id, harmonized_path, manifest_path)

    assert not any(path.is_file() for path in second_scratch._base_dir.rglob("*"))

    # When another operation opens the published artifacts.
    with (
        open_generated_artifact(workflow_storage, user, meta.file_id, WorkflowFile.HARMONIZED_OUTPUT) as output,
        open_generated_artifact(
            workflow_storage, user, meta.file_id, WorkflowFile.HARMONIZATION_MANIFEST_BASE
        ) as manifest,
    ):
        # Then it can read both files without populating the second scratch directory.
        assert output and output.read_bytes() == b"diagnosis\nbeta\n"
        assert manifest and manifest.read_bytes() == b"fake parquet bytes"
        assert not any(path.is_file() for path in second_scratch._base_dir.rglob("*"))


async def test_durable_stage_three_artifacts_override_unpublished_scratch(tmp_path: Path) -> None:
    # Given an approved durable result and a stale worker's unpublished local files.
    user = UserContext(user_id="alice")
    workflow_storage = LocalWorkflowStorage(tmp_path / "workflow")
    scratch = UploadStorage(tmp_path / "scratch", UploadConstraints(max_bytes=10_000))
    meta = await scratch.store(InMemoryUpload(b"diagnosis\nalpha\n"), dataset_workflow_id())
    workflow_storage.create_workflow(user, meta.dataset_workflow_id)
    save_upload_artifacts(workflow_storage, user, scratch, meta)

    output_path = scratch.harmonized_path_for(meta.file_id, meta.saved_path)
    manifest_path = tmp_path / "generated.parquet"
    output_path.write_bytes(b"diagnosis\napproved\n")
    manifest_path.write_bytes(b"approved manifest")
    save_harmonized_artifacts(workflow_storage, user, meta.file_id, output_path, manifest_path)

    output_path.write_bytes(b"diagnosis\nstale worker\n")
    manifest_path.write_bytes(b"stale manifest")

    # When the reviewer opens the published versions.
    with (
        open_generated_artifact(workflow_storage, user, meta.file_id, WorkflowFile.HARMONIZED_OUTPUT) as output,
        open_generated_artifact(
            workflow_storage, user, meta.file_id, WorkflowFile.HARMONIZATION_MANIFEST_BASE
        ) as manifest,
    ):
        # Then both contain the last approved values.
        assert output and output.read_bytes() == b"diagnosis\napproved\n"
        assert manifest and manifest.read_bytes() == b"approved manifest"


async def test_open_upload_rejects_metadata_from_another_workflow(tmp_path: Path) -> None:
    # Given an upload whose stored metadata mistakenly names a different workflow.
    user = UserContext("owner")
    workflow = dataset_workflow_id()
    uploads = UploadStorage(tmp_path / "uploads", UploadConstraints(max_bytes=1000))
    storage = LocalWorkflowStorage(tmp_path / "durable")
    storage.create_workflow(user, workflow)
    meta = await uploads.store(InMemoryUpload(b"value\none\n"), workflow)
    metadata = uploads.metadata_payload(meta)
    metadata["file_id"] = dataset_workflow_id("b" * 32)
    storage.write_json(user, workflow, WorkflowFile.UPLOAD_METADATA, metadata, expected_version=None)

    # When the first workflow opens its source file.
    with (
        pytest.raises(UploadMetadataUnreadableError) as failure,
        open_upload_artifact(uploads, storage, user, workflow),
    ):
        pytest.fail("mismatched metadata exposed a source")

    # Then recovery identifies the requested workflow.
    assert failure.value.args == (workflow,)
