"""S3 implementation of typed workflow artifact storage.

Axis of change: translating workflow artifact operations into S3 object reads,
writes, conditional updates, and temporary local materialization.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol

from botocore.exceptions import ClientError

from .workflow_storage import (
    JsonValue,
    StoredArtifact,
    StoredJson,
    UserContext,
    VersionToken,
    WorkflowAccessDeniedError,
    WorkflowArtifactNotFoundError,
    WorkflowArtifactTypeError,
    WorkflowConflictError,
    WorkflowFile,
    WorkflowMetadata,
    WorkflowNotFoundError,
    WorkflowStorageError,
)

_CONTENT_TYPE_JSON = "application/json"
_WORKFLOWS_PREFIX = "workflows"
_METADATA_KEY = "metadata.json"
_JSON_PREFIX = "json"
_ARTIFACT_PREFIX = "artifacts"


class S3WorkflowClient(Protocol):
    def put_object(self, **kwargs: object) -> dict[str, object]: ...

    def get_object(self, **kwargs: object) -> dict[str, object]: ...

    def head_object(self, **kwargs: object) -> dict[str, object]: ...

    def delete_object(self, **kwargs: object) -> dict[str, object]: ...

    def list_objects_v2(self, **kwargs: object) -> dict[str, object]: ...


@dataclass(frozen=True)
class S3WorkflowStorage:
    """WorkflowStorage implementation backed by one S3 bucket/prefix."""

    bucket: str
    client: S3WorkflowClient
    prefix: str = ""

    def create_workflow(self, user: UserContext, file_id: str | None = None) -> WorkflowMetadata:
        if file_id is None:
            raise WorkflowStorageError("S3 workflow creation requires a caller-supplied file_id")
        metadata = WorkflowMetadata.create(user, file_id)
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=self._metadata_key(file_id),
                Body=_json_bytes(metadata.to_store()),
                ContentType=_CONTENT_TYPE_JSON,
                IfNoneMatch="*",
            )
        except ClientError as exc:
            if _is_precondition_failed(exc):
                raise WorkflowConflictError(f"Workflow already exists: {file_id}") from exc
            raise
        return metadata

    def read_json(self, user: UserContext, file_id: str, kind: WorkflowFile) -> StoredJson | None:
        self._require_json_kind(kind)
        self._require_access(user, file_id)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self._json_key(file_id, kind))
        except ClientError as exc:
            if _is_not_found(exc):
                return None
            raise
        return StoredJson(
            data=json.loads(_body_bytes(response).decode("utf-8")),
            version=_version_from_response(response),
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
        key = self._json_key(file_id, kind)
        self._check_write_version(key, kind, expected_version)
        kwargs: dict[str, object] = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": _json_bytes(data),
            "ContentType": _CONTENT_TYPE_JSON,
        }
        if expected_version is None:
            kwargs["IfNoneMatch"] = "*"
        else:
            kwargs["IfMatch"] = expected_version.value
        try:
            response = self.client.put_object(**kwargs)
        except ClientError as exc:
            if _is_precondition_failed(exc):
                raise WorkflowConflictError(f"Artifact version changed: {kind.value}") from exc
            raise
        return StoredJson(data=data, version=_version_from_response(response))

    def delete_json(self, user: UserContext, file_id: str, kind: WorkflowFile) -> bool:
        self._require_json_kind(kind)
        self._require_access(user, file_id)
        key = self._json_key(file_id, kind)
        existed = self._object_version(key) is not None
        self.client.delete_object(Bucket=self.bucket, Key=key)
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
        key = self._artifact_key(file_id, kind, source_path.suffix)
        try:
            response = self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=source_path.read_bytes(),
                IfNoneMatch="*",
            )
        except ClientError as exc:
            if _is_precondition_failed(exc):
                raise WorkflowConflictError(f"Artifact already exists: {kind.value}") from exc
            raise
        return StoredArtifact(kind=kind, version=_version_from_response(response), suffix=source_path.suffix)

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
        for key in self._existing_artifact_keys(file_id, kind):
            self.client.delete_object(Bucket=self.bucket, Key=key)
        response = self.client.put_object(
            Bucket=self.bucket,
            Key=self._artifact_key(file_id, kind, source_path.suffix),
            Body=source_path.read_bytes(),
        )
        return StoredArtifact(kind=kind, version=_version_from_response(response), suffix=source_path.suffix)

    @contextmanager
    def materialize_artifact(
        self,
        user: UserContext,
        file_id: str,
        kind: WorkflowFile,
    ) -> Generator[Path]:
        self._require_artifact_kind(kind)
        self._require_access(user, file_id)
        key = self._existing_artifact_key(file_id, kind)
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        suffix = Path(key).suffix
        with NamedTemporaryFile("wb", suffix=suffix, delete=False) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(_body_bytes(response))
        try:
            yield temp_path
        finally:
            temp_path.unlink(missing_ok=True)

    def _require_access(self, user: UserContext, file_id: str) -> WorkflowMetadata:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self._metadata_key(file_id))
        except ClientError as exc:
            if _is_not_found(exc):
                raise WorkflowNotFoundError(file_id) from exc
            raise
        metadata = WorkflowMetadata.from_store(json.loads(_body_bytes(response).decode("utf-8")))
        if metadata is None:
            raise WorkflowStorageError(f"Workflow metadata is unreadable: {file_id}")
        if metadata.owner_user_id != user.user_id and not user.is_admin:
            raise WorkflowAccessDeniedError(file_id)
        return metadata

    def _check_write_version(
        self,
        key: str,
        kind: WorkflowFile,
        expected_version: VersionToken | None,
    ) -> None:
        current_version = self._object_version(key)
        if current_version is None:
            if expected_version is not None:
                raise WorkflowConflictError(f"Artifact does not exist: {kind.value}")
            return
        if not kind.is_mutable:
            raise WorkflowConflictError(f"Artifact is create-once: {kind.value}")
        if expected_version is None or expected_version != current_version:
            raise WorkflowConflictError(f"Artifact version changed: {kind.value}")

    def _object_version(self, key: str) -> VersionToken | None:
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _is_not_found(exc):
                return None
            raise
        return _version_from_response(response)

    def _existing_artifact_key(self, file_id: str, kind: WorkflowFile) -> str:
        keys = self._existing_artifact_keys(file_id, kind)
        if len(keys) != 1:
            raise WorkflowArtifactNotFoundError(f"Artifact not found: {kind.value}")
        return keys[0]

    def _existing_artifact_keys(self, file_id: str, kind: WorkflowFile) -> list[str]:
        prefix = self._artifact_prefix(file_id, kind)
        response = self.client.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
        return _listed_keys(response)

    def _metadata_key(self, file_id: str) -> str:
        return self._workflow_key(file_id, _METADATA_KEY)

    def _json_key(self, file_id: str, kind: WorkflowFile) -> str:
        return self._workflow_key(file_id, _JSON_PREFIX, f"{kind.value}.json")

    def _artifact_key(self, file_id: str, kind: WorkflowFile, suffix: str) -> str:
        return self._workflow_key(file_id, _ARTIFACT_PREFIX, f"{kind.value}{suffix.lower()}")

    def _artifact_prefix(self, file_id: str, kind: WorkflowFile) -> str:
        return self._workflow_key(file_id, _ARTIFACT_PREFIX, kind.value)

    def _workflow_key(self, file_id: str, *parts: str) -> str:
        if "/" in file_id or file_id in {"", ".", ".."}:
            raise WorkflowStorageError(f"Invalid workflow id: {file_id}")
        return "/".join([*_prefix_parts(self.prefix), _WORKFLOWS_PREFIX, file_id, *parts])

    def _require_json_kind(self, kind: WorkflowFile) -> None:
        if not kind.is_json:
            raise WorkflowArtifactTypeError(f"Not a JSON artifact: {kind.value}")

    def _require_artifact_kind(self, kind: WorkflowFile) -> None:
        if kind.is_json:
            raise WorkflowArtifactTypeError(f"Not a file artifact: {kind.value}")


def _prefix_parts(prefix: str) -> list[str]:
    cleaned = prefix.strip("/")
    return [] if not cleaned else cleaned.split("/")


def _json_bytes(data: JsonValue) -> bytes:
    return json.dumps(data, indent=2, default=str).encode("utf-8")


def _body_bytes(response: dict[str, object]) -> bytes:
    body = response.get("Body")
    read = getattr(body, "read", None)
    if not callable(read):
        raise WorkflowStorageError("S3 response body is unreadable")
    raw = read()
    if not isinstance(raw, bytes):
        raise WorkflowStorageError("S3 response body did not return bytes")
    return raw


def _listed_keys(response: dict[str, object]) -> list[str]:
    contents = response.get("Contents", [])
    if not isinstance(contents, list):
        return []
    keys: list[str] = []
    for item in contents:
        if isinstance(item, dict):
            key = item.get("Key")
            if isinstance(key, str):
                keys.append(key)
    return keys


def _version_from_response(response: dict[str, object]) -> VersionToken:
    etag = response.get("ETag")
    if not isinstance(etag, str) or not etag:
        raise WorkflowStorageError("S3 response did not include an ETag")
    return VersionToken(etag)


def _is_not_found(exc: ClientError) -> bool:
    code = str(exc.response.get("Error", {}).get("Code", ""))
    return code in {"NoSuchKey", "404", "NotFound"}


def _is_precondition_failed(exc: ClientError) -> bool:
    code = str(exc.response.get("Error", {}).get("Code", ""))
    return code in {"PreconditionFailed", "412"}


__all__ = [
    "S3WorkflowClient",
    "S3WorkflowStorage",
]
