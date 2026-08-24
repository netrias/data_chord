"""Typed workflow artifact storage with ownership and version checks.

Axis of change: how durable workflow artifacts are named, authorized, and
versioned across local and hosted storage backends.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable, Generator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol

from src.domain.dataset_workflow_ids import DatasetWorkflowId, dataset_workflow_id_from_value

STORAGE_SCHEMA_VERSION = 1
_WORKFLOWS_DIR = "workflows"
_METADATA_FILE = "metadata.json"
_JSON_DIR = "json"
_ARTIFACT_DIR = "artifacts"
_SHA256_PREFIX = "sha256:"

_FIELD_FILE_ID = "file_id"
_FIELD_OWNER_USER_ID = "owner_user_id"
_FIELD_CREATED_AT = "created_at"
_FIELD_LAST_ACCESSED_AT = "last_accessed_at"
_FIELD_STORAGE_SCHEMA_VERSION = "storage_schema_version"
_LOCKS_DIR = ".workflow-locks"
_CLEANUP_LOCK_FILE = ".cleanup.lock"
_UPLOAD_LOCK_FILE = ".upload.lock"

JsonValue = Mapping[str, object] | list[object] | str | int | float | bool | None


class WorkflowFile(str, Enum):
    """Known durable workflow artifacts.

    The storage layer owns the mapping from these names to local paths or S3
    keys. Callers should not build paths from file IDs.
    """

    ORIGINAL_UPLOAD = "original_upload"
    UPLOAD_METADATA = "upload_metadata"
    HARMONIZATION_MANIFEST_BASE = "harmonization_manifest_base"
    HARMONIZED_OUTPUT = "harmonized_output"
    PV_MANIFEST = "pv_manifest"
    CDE_MAPPING = "cde_mapping"
    STAGE_THREE_JOB = "stage_three_job"
    WORKFLOW_STATE = "workflow_state"
    REVIEW_OVERRIDES = "review_overrides"

    @property
    def is_json(self) -> bool:
        return self in {
            WorkflowFile.UPLOAD_METADATA,
            WorkflowFile.PV_MANIFEST,
            WorkflowFile.CDE_MAPPING,
            WorkflowFile.STAGE_THREE_JOB,
            WorkflowFile.WORKFLOW_STATE,
            WorkflowFile.REVIEW_OVERRIDES,
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

    dataset_workflow_id: DatasetWorkflowId
    owner_user_id: str
    created_at: datetime
    last_accessed_at: datetime
    storage_schema_version: int = STORAGE_SCHEMA_VERSION

    @classmethod
    def create(cls, user: UserContext, dataset_workflow_id: DatasetWorkflowId | str) -> WorkflowMetadata:
        created_at = datetime.now(UTC)
        return cls(
            dataset_workflow_id=dataset_workflow_id_from_value(dataset_workflow_id),
            owner_user_id=user.user_id,
            created_at=created_at,
            last_accessed_at=created_at,
        )

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
        if (
            created_at is None
            or isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
        ):
            return None
        if schema_version != STORAGE_SCHEMA_VERSION:
            return None
        if _FIELD_LAST_ACCESSED_AT not in payload:
            last_accessed_at = created_at
        else:
            last_accessed_at = _datetime_from_store(payload[_FIELD_LAST_ACCESSED_AT])
            if last_accessed_at is None or last_accessed_at < created_at:
                return None
        try:
            return cls(
                dataset_workflow_id=dataset_workflow_id_from_value(file_id),
                owner_user_id=owner_user_id,
                created_at=created_at,
                last_accessed_at=last_accessed_at,
                storage_schema_version=schema_version,
            )
        except ValueError:
            return None

    def accessed_at(self, accessed_at: datetime) -> WorkflowMetadata:
        return WorkflowMetadata(
            dataset_workflow_id=self.dataset_workflow_id,
            owner_user_id=self.owner_user_id,
            created_at=self.created_at,
            last_accessed_at=accessed_at,
            storage_schema_version=self.storage_schema_version,
        )

    def to_store(self) -> dict[str, object]:
        return {
            _FIELD_FILE_ID: self.dataset_workflow_id,
            _FIELD_OWNER_USER_ID: self.owner_user_id,
            _FIELD_CREATED_AT: self.created_at.isoformat(),
            _FIELD_LAST_ACCESSED_AT: self.last_accessed_at.isoformat(),
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


@dataclass(frozen=True)
class StoredWorkflowUsage:
    """Validated workflow metadata plus its current logical file size."""

    metadata: WorkflowMetadata
    size_bytes: int


@dataclass(frozen=True)
class WorkflowInventory:
    """Portable workflow disk use and safe cleanup candidates."""

    usage_bytes: int
    workflows: tuple[StoredWorkflowUsage, ...]


class WorkflowStorageError(Exception):
    """Base class for workflow storage failures."""


class WorkflowNotFoundError(WorkflowStorageError):
    """Raised when a workflow metadata record does not exist."""


class WorkflowAccessDeniedError(WorkflowStorageError):
    """Raised when a user cannot access a workflow."""


class WorkflowConflictError(WorkflowStorageError):
    """Raised when create-once or optimistic version checks fail."""


class WorkflowJsonUnreadableError(WorkflowStorageError):
    """Raised when durable workflow JSON cannot be decoded."""


class WorkflowArtifactNotFoundError(WorkflowStorageError):
    """Raised when a known workflow artifact has not been stored."""


class WorkflowArtifactTypeError(WorkflowStorageError):
    """Raised when a JSON operation targets a file artifact, or vice versa."""


class WorkflowArtifactSuffixError(WorkflowStorageError):
    """Raised when a file artifact has no usable suffix."""


class WorkflowStorage(Protocol):
    """Storage contract shared by local and hosted implementations."""

    def create_workflow(
        self,
        user: UserContext,
        dataset_workflow_id: DatasetWorkflowId,
    ) -> WorkflowMetadata: ...

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
        self._locks_dir = self._base_dir / _LOCKS_DIR
        self._workflow_dir.mkdir(parents=True, exist_ok=True)
        self._locks_dir.mkdir(parents=True, exist_ok=True)

    def create_workflow(self, user: UserContext, dataset_workflow_id: DatasetWorkflowId) -> WorkflowMetadata:
        workflow_dir = self._path_for_workflow(dataset_workflow_id)
        workflow_dir.mkdir(parents=True, exist_ok=True)
        metadata = WorkflowMetadata.create(user, dataset_workflow_id)
        metadata_path = workflow_dir / _METADATA_FILE
        try:
            # Exclusive create preserves the owner record as the source of truth
            # when retries or duplicate uploads race on the same workflow id.
            with metadata_path.open("x", encoding="utf-8") as handle:
                json.dump(metadata.to_store(), handle, indent=2)
        except FileExistsError as exc:
            raise WorkflowConflictError(f"Workflow already exists: {dataset_workflow_id}") from exc
        return metadata

    def read_json(self, user: UserContext, file_id: str, kind: WorkflowFile) -> StoredJson | None:
        self._require_json_kind(kind)
        with self._workflow_lock(file_id):
            self._require_access_locked(user, file_id)
            path = self._json_path(file_id, kind)
            if not path.exists():
                return None
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise WorkflowJsonUnreadableError(
                    f"Workflow JSON is unreadable: {kind.value}"
                ) from exc
            return StoredJson(data=data, version=_version_for_file(path))

    def write_json(
        self,
        user: UserContext,
        file_id: str,
        kind: WorkflowFile,
        data: JsonValue,
        expected_version: VersionToken | None = None,
    ) -> StoredJson:
        self._require_json_kind(kind)
        with self._workflow_lock(file_id):
            self._require_access_locked(user, file_id)
            path = self._json_path(file_id, kind)
            self._check_write_version(path, kind, expected_version)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._write_json_atomic(path, data)
            return StoredJson(data=data, version=_version_for_file(path))

    def delete_json(self, user: UserContext, file_id: str, kind: WorkflowFile) -> bool:
        self._require_json_kind(kind)
        with self._workflow_lock(file_id):
            self._require_access_locked(user, file_id)
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
        suffix = artifact_suffix(source_path)
        if self._existing_artifact_paths(file_id, kind):
            raise WorkflowConflictError(f"Artifact already exists: {kind.value}")
        path = self._artifact_path(file_id, kind, suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._copy_artifact_create_once(source_path, path)
        except FileExistsError as exc:
            raise WorkflowConflictError(f"Artifact already exists: {kind.value}") from exc
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
        suffix = artifact_suffix(source_path)
        existing_paths = self._existing_artifact_paths(file_id, kind)
        if len(existing_paths) > 1:
            raise WorkflowConflictError(f"Artifact has multiple suffix variants: {kind.value}")
        if existing_paths:
            existing_path = existing_paths[0]
            if kind == WorkflowFile.ORIGINAL_UPLOAD:
                raise WorkflowConflictError(f"Artifact is create-once: {kind.value}")
            if existing_path.suffix.lower() != suffix:
                raise WorkflowConflictError(f"Artifact suffix changed: {kind.value}")
            path = existing_path
        else:
            path = self._artifact_path(file_id, kind, suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
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

    def workflow_inventory(self) -> WorkflowInventory:
        """Measure portable workflow files and return validated cleanup candidates."""
        usage_bytes = 0
        workflows: list[StoredWorkflowUsage] = []
        for workflow_dir in self._workflow_dir.iterdir():
            if workflow_dir.is_symlink() or not workflow_dir.is_dir():
                continue
            size_bytes = _directory_size(workflow_dir)
            usage_bytes += size_bytes
            try:
                metadata = self._read_metadata(workflow_dir.name)
            except WorkflowJsonUnreadableError:
                continue
            if metadata is None or metadata.dataset_workflow_id != workflow_dir.name:
                continue
            workflows.append(StoredWorkflowUsage(metadata=metadata, size_bytes=size_bytes))
        return WorkflowInventory(usage_bytes=usage_bytes, workflows=tuple(workflows))

    def available_bytes(self) -> int:
        return shutil.disk_usage(self._base_dir).free

    def filesystem_capacity_bytes(self) -> int:
        return shutil.disk_usage(self._base_dir).total

    def acquire_upload_lease(self) -> Callable[[], None]:
        """Block until this volume reserves one upload, then return its release operation."""
        import fcntl

        handle = (self._base_dir / _UPLOAD_LOCK_FILE).open("a+b")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except Exception:
            handle.close()
            raise
        released = False

        def _release() -> None:
            nonlocal released
            if released:
                return
            released = True
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

        return _release

    def delete_workflow_if_last_accessed(
        self,
        file_id: DatasetWorkflowId,
        expected_last_accessed_at: datetime,
        delete_scratch: Callable[[DatasetWorkflowId], None],
    ) -> bool:
        """Delete one unchanged workflow while excluding concurrent access."""
        with self._workflow_lock(file_id):
            metadata = self._read_metadata(file_id)
            if metadata is None or metadata.last_accessed_at != expected_last_accessed_at:
                return False
            delete_scratch(file_id)
            workflow_dir = self._path_for_workflow(file_id)
            shutil.rmtree(workflow_dir)
            return True

    @contextmanager
    def try_cleanup_lease(self) -> Generator[bool]:
        """Allow one cleanup operation for this volume across local processes."""
        import fcntl

        lock_path = self._base_dir / _CLEANUP_LOCK_FILE
        with lock_path.open("a+b") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

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
        if not paths:
            raise WorkflowArtifactNotFoundError(f"Artifact not found: {kind.value}")
        if len(paths) > 1:
            raise WorkflowConflictError(f"Artifact has multiple suffix variants: {kind.value}")
        return paths[0]

    def _existing_artifact_paths(self, file_id: str, kind: WorkflowFile) -> list[Path]:
        artifact_dir = self._path_for_workflow(file_id) / _ARTIFACT_DIR
        if not artifact_dir.is_dir():
            return []
        return sorted(
            path
            for path in artifact_dir.iterdir()
            if path.is_file() and artifact_suffix_from_name(kind, path.name) is not None
        )

    def _require_access(self, user: UserContext, file_id: str) -> WorkflowMetadata:
        with self._workflow_lock(file_id):
            return self._require_access_locked(user, file_id)

    def _require_access_locked(self, user: UserContext, file_id: str) -> WorkflowMetadata:
        """Authorize and refresh metadata while the caller holds the workflow lock."""
        metadata_path = self._metadata_path(file_id)
        metadata = self._read_metadata(file_id)
        if metadata is None:
            if metadata_path.exists():
                raise WorkflowJsonUnreadableError("Workflow metadata is unreadable")
            raise WorkflowNotFoundError(file_id)
        if metadata.owner_user_id != user.user_id and not user.is_admin:
            raise WorkflowAccessDeniedError(file_id)
        accessed = metadata.accessed_at(datetime.now(UTC))
        self._write_json_atomic(metadata_path, accessed.to_store())
        return accessed

    def _read_metadata(self, file_id: str) -> WorkflowMetadata | None:
        metadata_path = self._metadata_path(file_id)
        if metadata_path.is_symlink() or not metadata_path.is_file():
            return None
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except OSError:
            return None
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise WorkflowJsonUnreadableError("Workflow metadata is unreadable") from exc
        return WorkflowMetadata.from_store(payload)

    @contextmanager
    def _workflow_lock(self, file_id: str) -> Generator[None]:
        import fcntl

        lock_path = self._locks_dir / f"{dataset_workflow_id_from_value(file_id)}.lock"
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _check_write_version(
        self,
        path: Path,
        kind: WorkflowFile,
        expected_version: VersionToken | None,
    ) -> None:
        # Local storage mirrors S3 conditional writes so both backends reject
        # lost updates instead of letting the last writer win by accident.
        if not path.exists():
            if expected_version is not None:
                raise WorkflowConflictError(f"Artifact does not exist: {kind.value}")
            return
        current_version = _version_for_file(path)
        if expected_version is None or expected_version != current_version:
            raise WorkflowConflictError(f"Artifact version changed: {kind.value}")

    def _write_json_atomic(self, path: Path, data: JsonValue) -> None:
        content = json.dumps(data, indent=2, default=str)
        # Write beside the target and replace in one step so readers never see a
        # partially written JSON document.
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(content)
        temp_path.replace(path)

    def _copy_artifact_atomic(self, source_path: Path, path: Path) -> None:
        with NamedTemporaryFile("wb", dir=path.parent, delete=False) as temp_file:
            temp_path = Path(temp_file.name)
        try:
            shutil.copy2(source_path, temp_path)
            temp_path.replace(path)
        finally:
            temp_path.unlink(missing_ok=True)

    def _copy_artifact_create_once(self, source_path: Path, path: Path) -> None:
        with NamedTemporaryFile("wb", dir=path.parent, delete=False) as temp_file:
            temp_path = Path(temp_file.name)
        try:
            shutil.copy2(source_path, temp_path)
            os.link(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)

    def _require_json_kind(self, kind: WorkflowFile) -> None:
        if not kind.is_json:
            raise WorkflowArtifactTypeError(f"Not a JSON artifact: {kind.value}")

    def _require_artifact_kind(self, kind: WorkflowFile) -> None:
        if kind.is_json:
            raise WorkflowArtifactTypeError(f"Not a file artifact: {kind.value}")


def _version_for_file(path: Path) -> VersionToken:
    # Content hashes give local files the same optimistic-write shape as S3
    # ETags without relying on filesystem timestamps.
    return VersionToken(f"{_SHA256_PREFIX}{_sha256_for_file(path)}")


def artifact_suffix(source_path: Path) -> str:
    if not source_path.is_file():
        raise WorkflowArtifactNotFoundError(f"Source artifact not found: {source_path}")
    suffix = source_path.suffix.lower()
    if not suffix or suffix == "." or "/" in suffix or "\\" in suffix:
        raise WorkflowArtifactSuffixError(f"Artifact source has an invalid suffix: {source_path}")
    return suffix


def artifact_suffix_from_name(kind: WorkflowFile, name: str) -> str | None:
    path = Path(name)
    if path.name != name or path.stem != kind.value:
        return None
    suffix = path.suffix.lower()
    if not suffix or suffix == "." or "/" in suffix or "\\" in suffix:
        return None
    return suffix


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
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else None


def _directory_size(path: Path) -> int:
    size_bytes = 0
    for root, directory_names, file_names in os.walk(path, followlinks=False):
        root_path = Path(root)
        directory_names[:] = [
            name for name in directory_names if not (root_path / name).is_symlink()
        ]
        for name in file_names:
            file_path = root_path / name
            if file_path.is_symlink():
                continue
            try:
                size_bytes += file_path.stat().st_size
            except FileNotFoundError:
                continue
    return size_bytes


__all__ = [
    "LocalWorkflowStorage",
    "StoredArtifact",
    "StoredJson",
    "StoredWorkflowUsage",
    "UserContext",
    "VersionToken",
    "WorkflowAccessDeniedError",
    "WorkflowArtifactNotFoundError",
    "WorkflowArtifactSuffixError",
    "WorkflowArtifactTypeError",
    "WorkflowConflictError",
    "WorkflowFile",
    "WorkflowInventory",
    "WorkflowJsonUnreadableError",
    "WorkflowMetadata",
    "WorkflowNotFoundError",
    "WorkflowStorage",
    "WorkflowStorageError",
]
