"""Bridge legacy local upload files to durable typed workflow storage.

Axis of change: when workflow artifacts need to move between local scratch
paths and the configured durable workflow storage backend.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from src.domain.manifest import ManifestPayload, normalize_manifest
from src.storage import (
    LocalWorkflowStorage,
    UploadedFileMeta,
    UploadStorage,
    UserContext,
    WorkflowArtifactNotFoundError,
    WorkflowFile,
    WorkflowNotFoundError,
    WorkflowStorage,
)


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


def load_upload_artifact(
    upload_storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
) -> UploadedFileMeta | None:
    try:
        stored = workflow_storage.read_json(user, file_id, WorkflowFile.UPLOAD_METADATA)
    except WorkflowNotFoundError:
        # Ownerless scratch data is only a local-development compatibility path.
        # Hosted storage must never let a caller establish ownership by knowing
        # an old file id.
        return upload_storage.load(file_id) if _allow_ownerless_local_fallback(workflow_storage, user) else None
    local_meta = upload_storage.load(file_id)
    if stored is None or not isinstance(stored.data, Mapping):
        return local_meta if _allow_ownerless_local_fallback(workflow_storage, user) else None
    if local_meta is not None and local_meta.saved_path.exists():
        # Prefer the existing local file when present to avoid copying large
        # artifacts back out of durable storage on every stage transition.
        return upload_storage.restore_upload(stored.data, local_meta.saved_path)
    try:
        with workflow_storage.materialize_artifact(user, file_id, WorkflowFile.ORIGINAL_UPLOAD) as source_path:
            return upload_storage.restore_upload(stored.data, source_path)
    except WorkflowArtifactNotFoundError:
        return local_meta


def save_mapping_manifest(
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
    manifest: ManifestPayload | Mapping[str, object],
) -> None:
    _upsert_json(workflow_storage, user, file_id, WorkflowFile.MAPPING_MANIFEST, normalize_manifest(manifest))


def load_mapping_manifest(
    upload_storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
) -> ManifestPayload | None:
    try:
        stored = workflow_storage.read_json(user, file_id, WorkflowFile.MAPPING_MANIFEST)
    except WorkflowNotFoundError:
        if _allow_ownerless_local_fallback(workflow_storage, user):
            return upload_storage.load_manifest(file_id)
        return None
    if stored is not None:
        return normalize_manifest(stored.data)
    if _allow_ownerless_local_fallback(workflow_storage, user):
        return upload_storage.load_manifest(file_id)
    return None


def save_harmonized_artifacts(
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
    harmonized_path: Path,
    manifest_path: Path | None,
) -> None:
    workflow_storage.write_artifact(user, file_id, WorkflowFile.HARMONIZED_OUTPUT, harmonized_path)
    if manifest_path is not None:
        workflow_storage.write_artifact(user, file_id, WorkflowFile.HARMONIZATION_MANIFEST_BASE, manifest_path)


def load_harmonized_output_path(
    upload_storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
    meta: UploadedFileMeta,
) -> Path | None:
    # Authorize against durable workflow metadata before consulting scratch.
    try:
        workflow_storage.read_json(user, file_id, WorkflowFile.UPLOAD_METADATA)
    except WorkflowNotFoundError:
        if _allow_ownerless_local_fallback(workflow_storage, user):
            return upload_storage.load_harmonized_path(file_id)
        raise
    try:
        with workflow_storage.materialize_artifact(user, file_id, WorkflowFile.HARMONIZED_OUTPUT) as source_path:
            return upload_storage.restore_harmonized_output(file_id, meta.saved_path, source_path)
    except (WorkflowArtifactNotFoundError, WorkflowNotFoundError):
        return None


def load_harmonization_manifest_path(
    upload_storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
) -> Path | None:
    # Authorize against durable workflow metadata before consulting scratch.
    try:
        workflow_storage.read_json(user, file_id, WorkflowFile.UPLOAD_METADATA)
    except WorkflowNotFoundError:
        if _allow_ownerless_local_fallback(workflow_storage, user):
            return upload_storage.load_harmonization_manifest_path(file_id)
        raise
    try:
        with workflow_storage.materialize_artifact(
            user,
            file_id,
            WorkflowFile.HARMONIZATION_MANIFEST_BASE,
        ) as source_path:
            return upload_storage.restore_harmonization_manifest(file_id, source_path)
    except (WorkflowArtifactNotFoundError, WorkflowNotFoundError):
        return None


def _upsert_json(
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
    kind: WorkflowFile,
    data: Mapping[str, object],
) -> None:
    # Write through the storage version token so browser retries do not erase a
    # newer artifact written by another request.
    existing = workflow_storage.read_json(user, file_id, kind)
    expected_version = existing.version if existing is not None else None
    workflow_storage.write_json(user, file_id, kind, data, expected_version=expected_version)


def _allow_ownerless_local_fallback(
    workflow_storage: WorkflowStorage,
    user: UserContext,
) -> bool:
    return isinstance(workflow_storage, LocalWorkflowStorage) and user.user_id == "local-user"


__all__ = [
    "load_harmonization_manifest_path",
    "load_harmonized_output_path",
    "load_mapping_manifest",
    "load_upload_artifact",
    "save_harmonized_artifacts",
    "save_mapping_manifest",
    "save_upload_artifacts",
    "save_upload_metadata",
]
