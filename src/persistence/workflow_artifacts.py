"""Move workflow artifacts between local scratch and durable storage.

Axis of change: when workflow artifacts need to move between local scratch
paths and the configured durable workflow storage backend.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator, Mapping
from contextlib import AbstractContextManager, ExitStack, asynccontextmanager, contextmanager, nullcontext
from pathlib import Path

from src.domain.dataset_workflow_ids import DatasetWorkflowId
from src.domain.upload_integrity import UploadFingerprint
from src.storage import (
    S3WorkflowStorage,
    StoredJson,
    UploadedFileMeta,
    UploadStorage,
    UserContext,
    VersionToken,
    WorkflowArtifactNotFoundError,
    WorkflowFile,
    WorkflowJsonUnreadableError,
    WorkflowNotFoundError,
    WorkflowStorage,
)
from src.storage.upload_storage import UploadStream


class UploadMetadataUnreadableError(Exception):
    """Raised when durable upload metadata cannot be decoded."""


def save_upload_artifacts(
    workflow_storage: WorkflowStorage,
    user: UserContext,
    upload_storage: UploadStorage,
    meta: UploadedFileMeta,
) -> None:
    _upsert_json(
        workflow_storage,
        user,
        meta.dataset_workflow_id,
        WorkflowFile.UPLOAD_METADATA,
        upload_storage.metadata_payload(meta),
    )
    workflow_storage.create_artifact(user, meta.dataset_workflow_id, WorkflowFile.ORIGINAL_UPLOAD, meta.saved_path)


def save_upload_metadata(
    workflow_storage: WorkflowStorage,
    user: UserContext,
    upload_storage: UploadStorage,
    meta: UploadedFileMeta,
) -> None:
    _upsert_json(
        workflow_storage,
        user,
        meta.dataset_workflow_id,
        WorkflowFile.UPLOAD_METADATA,
        upload_storage.metadata_payload(meta),
    )


def upload_workspace(upload_storage: UploadStorage, workflow_storage: WorkflowStorage) -> AbstractContextManager[None]:
    """Keep staged uploads and worker outputs private while S3 is their durable store."""
    if isinstance(workflow_storage, S3WorkflowStorage):
        return upload_storage.private_workspace()
    return nullcontext()


@asynccontextmanager
async def stage_upload(
    uploads: UploadStorage, storage: WorkflowStorage, file: UploadStream, file_id: DatasetWorkflowId,
    expected: UploadFingerprint | None = None,
) -> AsyncIterator[UploadedFileMeta]:
    """Keep the incoming file through its durable write and any preview reads."""
    with upload_workspace(uploads, storage):
        yield await uploads.store(file, file_id, expected)


def upload_is_available(uploads: UploadStorage, storage: WorkflowStorage, user: UserContext, file_id: str) -> bool:
    """Check admission without downloading the source that the worker will open."""
    try:
        if _read_upload_metadata(storage, user, file_id) is None:
            return False
        if storage.artifact_version(user, file_id, WorkflowFile.ORIGINAL_UPLOAD) is not None:
            return True
    except WorkflowNotFoundError:
        return False
    if isinstance(storage, S3WorkflowStorage):
        return False
    local = uploads.load(file_id)
    return local is not None and local.saved_path.is_file()


@contextmanager
def open_upload_artifact(
    upload_storage: UploadStorage, workflow_storage: WorkflowStorage, user: UserContext, file_id: str,
) -> Generator[UploadedFileMeta | None]:
    try:
        stored = _read_upload_metadata(workflow_storage, user, file_id)
    except WorkflowNotFoundError:
        stored = None
    if stored is None or not isinstance(stored.data, Mapping):
        yield None
        return
    if isinstance(workflow_storage, S3WorkflowStorage):
        with _open_artifact(workflow_storage, user, file_id, WorkflowFile.ORIGINAL_UPLOAD) as source:
            yield upload_storage.metadata_for_path(stored.data, source) if source else None
        return
    local_meta = upload_storage.load(file_id)
    if local_meta and local_meta.saved_path.exists():
        yield upload_storage.restore_upload(stored.data, local_meta.saved_path)
        return
    with _open_artifact(workflow_storage, user, file_id, WorkflowFile.ORIGINAL_UPLOAD) as source:
        yield upload_storage.restore_upload(stored.data, source) if source else local_meta


@contextmanager
def _open_artifact(
    storage: WorkflowStorage, user: UserContext, file_id: str, kind: WorkflowFile,
) -> Generator[Path | None]:
    with ExitStack() as files:
        try:
            path = files.enter_context(storage.materialize_artifact(user, file_id, kind))
        except WorkflowArtifactNotFoundError:
            path = None
        yield path


def save_harmonized_artifacts(
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
    harmonized_path: Path,
    manifest_path: Path | None,
    *,
    expected_harmonized_output_version: VersionToken | None = None,
    expected_manifest_version: VersionToken | None = None,
) -> None:
    workflow_storage.write_artifact(
        user,
        file_id,
        WorkflowFile.HARMONIZED_OUTPUT,
        harmonized_path,
        expected_version=expected_harmonized_output_version,
    )
    if manifest_path is not None:
        workflow_storage.write_artifact(
            user,
            file_id,
            WorkflowFile.HARMONIZATION_MANIFEST_BASE,
            manifest_path,
            expected_version=expected_manifest_version,
        )


def open_generated_artifact(
    storage: WorkflowStorage, user: UserContext, file_id: str, kind: WorkflowFile,
) -> AbstractContextManager[Path | None]:
    """Read the published artifact directly. Its storage backend owns the file lifetime."""
    _read_upload_metadata(storage, user, file_id)
    return _open_artifact(storage, user, file_id, kind)


def _upsert_json(
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
    kind: WorkflowFile,
    data: Mapping[str, object],
) -> None:
    # Write through the storage version token so browser retries do not erase a
    # newer artifact written by another request.
    try:
        existing = workflow_storage.read_json(user, file_id, kind)
    except WorkflowJsonUnreadableError as exc:
        raise UploadMetadataUnreadableError(file_id) from exc
    expected_version = existing.version if existing is not None else None
    workflow_storage.write_json(user, file_id, kind, data, expected_version=expected_version)


def _read_upload_metadata(
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
) -> StoredJson | None:
    try:
        stored = workflow_storage.read_json(user, file_id, WorkflowFile.UPLOAD_METADATA)
    except WorkflowJsonUnreadableError as exc:
        raise UploadMetadataUnreadableError(file_id) from exc
    if stored is not None and (not isinstance(stored.data, Mapping) or stored.data.get("file_id") != file_id):
        raise UploadMetadataUnreadableError(file_id)
    return stored


__all__ = [
    "UploadMetadataUnreadableError",
    "open_generated_artifact",
    "open_upload_artifact",
    "upload_workspace",
    "save_harmonized_artifacts",
    "save_upload_artifacts",
    "save_upload_metadata",
]
