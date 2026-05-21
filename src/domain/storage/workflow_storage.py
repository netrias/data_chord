"""Typed workflow artifact storage with ownership and version checks.

Axis of change: how durable workflow artifacts are named, authorized, and
versioned across local and hosted storage backends.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Generator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol
from uuid import uuid4

STORAGE_SCHEMA_VERSION = 1
_WORKFLOWS_DIR = "workflows"
_METADATA_FILE = "metadata.json"
_JSON_DIR = "json"
_ARTIFACT_DIR = "artifacts"
_SHA256_PREFIX = "sha256:"

_FIELD_FILE_ID = "file_id"
_FIELD_OWNER_USER_ID = "owner_user_id"
_FIELD_CREATED_AT = "created_at"
_FIELD_STORAGE_SCHEMA_VERSION = "storage_schema_version"

JsonValue = Mapping[str, object] | list[object] | str | int | float | bool | None


class WorkflowFile(str, Enum):
    """Known durable workflow artifacts.

    The storage layer owns the mapping from these names to local paths or S3
    keys. Callers should not build paths from file IDs.
    """

    ORIGINAL_UPLOAD = "original_upload"
    UPLOAD_METADATA = "upload_metadata"
    MAPPING_MANIFEST = "mapping_manifest"
    HARMONIZATION_MANIFEST_BASE = "harmonization_manifest_base"
    HARMONIZED_OUTPUT = "harmonized_output"
    PV_MANIFEST = "pv_manifest"
    CDE_MAPPING = "cde_mapping"
    STAGE_THREE_JOB = "stage_three_job"
    WORKFLOW_STATE = "workflow_state"
    REVIEW_OVERRIDES = "review_overrides"
    REVIEW_AUDIT = "review_audit"
    FINAL_BUNDLE = "final_bundle"

    @property
    def is_json(self) -> bool:
        return self in {
            WorkflowFile.UPLOAD_METADATA,
            WorkflowFile.MAPPING_MANIFEST,
            WorkflowFile.PV_MANIFEST,
            WorkflowFile.CDE_MAPPING,
            WorkflowFile.STAGE_THREE_JOB,
            WorkflowFile.WORKFLOW_STATE,
            WorkflowFile.REVIEW_OVERRIDES,
            WorkflowFile.REVIEW_AUDIT,
        }

    @property
    def is_mutable(self) -> bool:
        return self in {
            WorkflowFile.UPLOAD_METADATA,
            WorkflowFile.MAPPING_MANIFEST,
            WorkflowFile.CDE_MAPPING,
            WorkflowFile.STAGE_THREE_JOB,
            WorkflowFile.PV_MANIFEST,
            WorkflowFile.WORKFLOW_STATE,
            WorkflowFile.REVIEW_OVERRIDES,
            WorkflowFile.REVIEW_AUDIT,
        }


@dataclass(frozen=True)
class UserContext:
    """Authenticated user facts needed by storage authorization."""

    user_id: str
    email: str | None = None
    is_admin: bool = False


@dataclass(frozen=True)
class WorkflowMetadata:
    """Create-once owner record for a workflow."""

    file_id: str
    owner_user_id: str
    created_at: datetime
    storage_schema_version: int = STORAGE_SCHEMA_VERSION

    @classmethod
    def create(cls, user: UserContext, file_id: str) -> WorkflowMetadata:
        return cls(file_id=file_id, owner_user_id=user.user_id, created_at=datetime.now(UTC))

    @classmethod
    def from_store(cls, payload: object) -> WorkflowMetadata | None:
        if not isinstance(payload, Mapping):
            return None
        file_id = payload.get(_FIELD_FILE_ID)
        owner_user_id = payload.get(_FIELD_OWNER_USER_ID)
        created_at = _datetime_from_store(payload.get(_FIELD_CREATED_AT))
        schema_version = payload.get(_FIELD_STORAGE_SCHEMA_VERSION)
        if not isinstance(file_id, str) or not isinstance(owner_user_id, str):
            return None
        if created_at is None or not isinstance(schema_version, int):
            return None
        return cls(
            file_id=file_id,
            owner_user_id=owner_user_id,
            created_at=created_at,
            storage_schema_version=schema_version,
        )

    def to_store(self) -> dict[str, object]:
        return {
            _FIELD_FILE_ID: self.file_id,
            _FIELD_OWNER_USER_ID: self.owner_user_id,
            _FIELD_CREATED_AT: self.created_at.isoformat(),
            _FIELD_STORAGE_SCHEMA_VERSION: self.storage_schema_version,
        }


@dataclass(frozen=True)
class VersionToken:
    """Opaque storage version used for optimistic writes."""

    value: str


@dataclass(frozen=True)
class StoredJson:
    """JSON artifact plus the version that was read."""

    data: JsonValue
    version: VersionToken


@dataclass(frozen=True)
class StoredArtifact:
    """File artifact metadata returned after creation."""

    kind: WorkflowFile
    version: VersionToken
    suffix: str


class WorkflowStorageError(Exception):
    """Base class for workflow storage failures."""


class WorkflowNotFoundError(WorkflowStorageError):
    """Raised when a workflow metadata record does not exist."""


class WorkflowAccessDeniedError(WorkflowStorageError):
    """Raised when a user cannot access a workflow."""


class WorkflowConflictError(WorkflowStorageError):
    """Raised when create-once or optimistic version checks fail."""


class WorkflowArtifactNotFoundError(WorkflowStorageError):
    """Raised when a known workflow artifact has not been stored."""


class WorkflowArtifactTypeError(WorkflowStorageError):
    """Raised when a JSON operation targets a file artifact, or vice versa."""


class WorkflowStorage(Protocol):
    """Storage contract shared by local and hosted implementations."""

    def create_workflow(self, user: UserContext, file_id: str | None = None) -> WorkflowMetadata: ...

    def read_json(self, user: UserContext, file_id: str, kind: WorkflowFile) -> StoredJson | None: ...

    def write_json(
        self,
        user: UserContext,
        file_id: str,
        kind: WorkflowFile,
        data: JsonValue,
        expected_version: VersionToken | None = None,
    ) -> StoredJson: ...

    def delete_json(self, user: UserContext, file_id: str, kind: WorkflowFile) -> bool: ...

    def create_artifact(
        self,
        user: UserContext,
        file_id: str,
        kind: WorkflowFile,
        source_path: Path,
    ) -> StoredArtifact: ...

    def write_artifact(
        self,
        user: UserContext,
        file_id: str,
        kind: WorkflowFile,
        source_path: Path,
    ) -> StoredArtifact: ...

    def materialize_artifact(
        self,
        user: UserContext,
        file_id: str,
        kind: WorkflowFile,
    ) -> AbstractContextManager[Path]: ...


class LocalWorkflowStorage:
    """WorkflowStorage implementation backed by local files."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir.resolve()
        self._workflow_dir = self._base_dir / _WORKFLOWS_DIR
        self._workflow_dir.mkdir(parents=True, exist_ok=True)

    def create_workflow(self, user: UserContext, file_id: str | None = None) -> WorkflowMetadata:
        workflow_id = file_id or uuid4().hex
        workflow_dir = self._path_for_workflow(workflow_id)
        workflow_dir.mkdir(parents=True, exist_ok=True)
        metadata = WorkflowMetadata.create(user, workflow_id)
        metadata_path = workflow_dir / _METADATA_FILE
        try:
            with metadata_path.open("x", encoding="utf-8") as handle:
                json.dump(metadata.to_store(), handle, indent=2)
        except FileExistsError as exc:
            raise WorkflowConflictError(f"Workflow already exists: {workflow_id}") from exc
        return metadata

    def read_json(self, user: UserContext, file_id: str, kind: WorkflowFile) -> StoredJson | None:
        self._require_json_kind(kind)
        self._require_access(user, file_id)
        path = self._json_path(file_id, kind)
        if not path.exists():
            return None
        return StoredJson(
            data=json.loads(path.read_text(encoding="utf-8")),
            version=_version_for_file(path),
        )

    def write_json(
        self,
        user: UserContext,
        file_id: str,
        kind: WorkflowFile,
        data: JsonValue,
        expected_version: VersionToken | None = None,
    ) -> StoredJson:
        self._require_json_kind(kind)
        self._require_access(user, file_id)
        path = self._json_path(file_id, kind)
        self._check_write_version(path, kind, expected_version)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json_atomic(path, data)
        return StoredJson(data=data, version=_version_for_file(path))

    def delete_json(self, user: UserContext, file_id: str, kind: WorkflowFile) -> bool:
        self._require_json_kind(kind)
        self._require_access(user, file_id)
        path = self._json_path(file_id, kind)
        existed = path.exists()
        path.unlink(missing_ok=True)
        return existed

    def create_artifact(
        self,
        user: UserContext,
        file_id: str,
        kind: WorkflowFile,
        source_path: Path,
    ) -> StoredArtifact:
        self._require_artifact_kind(kind)
        self._require_access(user, file_id)
        if not source_path.is_file():
            raise WorkflowArtifactNotFoundError(f"Source artifact not found: {source_path}")
        path = self._artifact_path(file_id, kind, source_path.suffix)
        if path.exists():
            raise WorkflowConflictError(f"Artifact already exists: {kind.value}")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._copy_artifact_atomic(source_path, path)
        return StoredArtifact(kind=kind, version=_version_for_file(path), suffix=path.suffix)

    def write_artifact(
        self,
        user: UserContext,
        file_id: str,
        kind: WorkflowFile,
        source_path: Path,
    ) -> StoredArtifact:
        self._require_artifact_kind(kind)
        self._require_access(user, file_id)
        if not source_path.is_file():
            raise WorkflowArtifactNotFoundError(f"Source artifact not found: {source_path}")
        path = self._artifact_path(file_id, kind, source_path.suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        for existing_path in self._existing_artifact_paths(file_id, kind):
            if existing_path != path:
                existing_path.unlink(missing_ok=True)
        self._copy_artifact_atomic(source_path, path)
        return StoredArtifact(kind=kind, version=_version_for_file(path), suffix=path.suffix)

    @contextmanager
    def materialize_artifact(
        self,
        user: UserContext,
        file_id: str,
        kind: WorkflowFile,
    ) -> Generator[Path]:
        self._require_artifact_kind(kind)
        self._require_access(user, file_id)
        yield self._existing_artifact_path(file_id, kind)

    def _path_for_workflow(self, file_id: str) -> Path:
        path = (self._workflow_dir / file_id).resolve()
        if not path.is_relative_to(self._workflow_dir):
            raise WorkflowStorageError(f"Invalid workflow id: {file_id}")
        return path

    def _metadata_path(self, file_id: str) -> Path:
        return self._path_for_workflow(file_id) / _METADATA_FILE

    def _json_path(self, file_id: str, kind: WorkflowFile) -> Path:
        return self._path_for_workflow(file_id) / _JSON_DIR / f"{kind.value}.json"

    def _artifact_path(self, file_id: str, kind: WorkflowFile, suffix: str) -> Path:
        return self._path_for_workflow(file_id) / _ARTIFACT_DIR / f"{kind.value}{suffix.lower()}"

    def _existing_artifact_path(self, file_id: str, kind: WorkflowFile) -> Path:
        paths = self._existing_artifact_paths(file_id, kind)
        if len(paths) != 1:
            raise WorkflowArtifactNotFoundError(f"Artifact not found: {kind.value}")
        return paths[0]

    def _existing_artifact_paths(self, file_id: str, kind: WorkflowFile) -> list[Path]:
        return sorted((self._path_for_workflow(file_id) / _ARTIFACT_DIR).glob(f"{kind.value}*"))

    def _require_access(self, user: UserContext, file_id: str) -> WorkflowMetadata:
        metadata_path = self._metadata_path(file_id)
        if not metadata_path.exists():
            raise WorkflowNotFoundError(file_id)
        metadata = WorkflowMetadata.from_store(json.loads(metadata_path.read_text(encoding="utf-8")))
        if metadata is None:
            raise WorkflowStorageError(f"Workflow metadata is unreadable: {file_id}")
        if metadata.owner_user_id != user.user_id and not user.is_admin:
            raise WorkflowAccessDeniedError(file_id)
        return metadata

    def _check_write_version(
        self,
        path: Path,
        kind: WorkflowFile,
        expected_version: VersionToken | None,
    ) -> None:
        if not path.exists():
            if expected_version is not None:
                raise WorkflowConflictError(f"Artifact does not exist: {kind.value}")
            return
        if not kind.is_mutable:
            raise WorkflowConflictError(f"Artifact is create-once: {kind.value}")
        current_version = _version_for_file(path)
        if expected_version is None or expected_version != current_version:
            raise WorkflowConflictError(f"Artifact version changed: {kind.value}")

    def _write_json_atomic(self, path: Path, data: JsonValue) -> None:
        content = json.dumps(data, indent=2, default=str)
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(content)
        temp_path.replace(path)

    def _copy_artifact_atomic(self, source_path: Path, path: Path) -> None:
        with NamedTemporaryFile("wb", dir=path.parent, delete=False) as temp_file:
            temp_path = Path(temp_file.name)
        shutil.copy2(source_path, temp_path)
        temp_path.replace(path)

    def _require_json_kind(self, kind: WorkflowFile) -> None:
        if not kind.is_json:
            raise WorkflowArtifactTypeError(f"Not a JSON artifact: {kind.value}")

    def _require_artifact_kind(self, kind: WorkflowFile) -> None:
        if kind.is_json:
            raise WorkflowArtifactTypeError(f"Not a file artifact: {kind.value}")


def _version_for_file(path: Path) -> VersionToken:
    return VersionToken(f"{_SHA256_PREFIX}{_sha256_for_file(path)}")


def _sha256_for_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _datetime_from_store(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


__all__ = [
    "LocalWorkflowStorage",
    "StoredArtifact",
    "StoredJson",
    "UserContext",
    "VersionToken",
    "WorkflowAccessDeniedError",
    "WorkflowArtifactNotFoundError",
    "WorkflowArtifactTypeError",
    "WorkflowConflictError",
    "WorkflowFile",
    "WorkflowMetadata",
    "WorkflowNotFoundError",
    "WorkflowStorage",
    "WorkflowStorageError",
]
