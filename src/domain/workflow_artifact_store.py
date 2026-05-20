"""Bridge legacy local upload files to durable typed workflow storage.

Axis of change: when workflow artifacts need to move between local scratch
paths and the configured durable workflow storage backend.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from src.domain.manifest import ManifestPayload, normalize_manifest
from src.domain.storage import (
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
        meta.file_id,
        WorkflowFile.UPLOAD_METADATA,
        upload_storage.metadata_payload(meta),
    )
    workflow_storage.create_artifact(user, meta.file_id, WorkflowFile.ORIGINAL_UPLOAD, meta.saved_path)


def save_upload_metadata(
    workflow_storage: WorkflowStorage,
    user: UserContext,
    upload_storage: UploadStorage,
    meta: UploadedFileMeta,
) -> None:
    _upsert_json(
        workflow_storage,
        user,
        meta.file_id,
        WorkflowFile.UPLOAD_METADATA,
        upload_storage.metadata_payload(meta),
    )


def load_upload_artifact(
    upload_storage: UploadStorage,
    workflow_storage: WorkflowStorage,
    user: UserContext,
    file_id: str,
) -> UploadedFileMeta | None:
    local_meta = upload_storage.load(file_id)
    try:
        stored = workflow_storage.read_json(user, file_id, WorkflowFile.UPLOAD_METADATA)
    except WorkflowNotFoundError:
        return local_meta
    if stored is None or not isinstance(stored.data, Mapping):
        return local_meta
    if local_meta is not None and local_meta.saved_path.exists():
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
    local_manifest = upload_storage.load_manifest(file_id)
    if local_manifest is not None:
        return local_manifest
    try:
        stored = workflow_storage.read_json(user, file_id, WorkflowFile.MAPPING_MANIFEST)
    except WorkflowNotFoundError:
        return None
    if stored is None:
        return None
    manifest = normalize_manifest(stored.data)
    upload_storage.save_manifest(file_id, manifest)
    return manifest


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
    path = upload_storage.load_harmonized_path(file_id)
    if path is not None:
        return path
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
    path = upload_storage.load_harmonization_manifest_path(file_id)
    if path is not None:
        return path
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
    existing = workflow_storage.read_json(user, file_id, kind)
    expected_version = existing.version if existing is not None else None
    workflow_storage.write_json(user, file_id, kind, data, expected_version=expected_version)


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
