"""Contract tests for S3 workflow storage behavior."""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from src.domain.dataset_workflow_ids import DatasetWorkflowId, dataset_workflow_id_from_string
from src.storage import (
    S3WorkflowStorage,
    UserContext,
    WorkflowAccessDeniedError,
    WorkflowConflictError,
    WorkflowFile,
    WorkflowJsonUnreadableError,
    WorkflowNotFoundError,
)


def dataset_workflow_id(raw: str = "a" * 32) -> DatasetWorkflowId:
    return dataset_workflow_id_from_string(raw)


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.failed_put_key: str | None = None
        self.replacement_before_delete: tuple[str, bytes] | None = None

    def put_object(self, **kwargs: object) -> dict[str, object]:
        key = _key(kwargs)
        if key == self.failed_put_key:
            raise _client_error("InternalError")
        body = kwargs.get("Body")
        if not isinstance(body, bytes):
            raise AssertionError("FakeS3Client expects bytes bodies")
        if kwargs.get("IfNoneMatch") == "*" and key in self.objects:
            raise _client_error("PreconditionFailed")
        if_match = kwargs.get("IfMatch")
        if isinstance(if_match, str) and self.objects.get(key, (b"", ""))[1] != if_match:
            raise _client_error("PreconditionFailed")
        etag = _etag(body)
        self.objects[key] = (body, etag)
        return {"ETag": etag}

    def get_object(self, **kwargs: object) -> dict[str, object]:
        key = _key(kwargs)
        if key not in self.objects:
            raise _client_error("NoSuchKey")
        body, etag = self.objects[key]
        return {"Body": BytesIO(body), "ETag": etag}

    def head_object(self, **kwargs: object) -> dict[str, object]:
        key = _key(kwargs)
        if key not in self.objects:
            raise _client_error("NoSuchKey")
        return {"ETag": self.objects[key][1]}

    def delete_object(self, **kwargs: object) -> dict[str, object]:
        key = _key(kwargs)
        if self.replacement_before_delete is not None:
            replacement_key, body = self.replacement_before_delete
            if replacement_key == key:
                self.objects[key] = (body, _etag(body))
                self.replacement_before_delete = None
        if_match = kwargs.get("IfMatch")
        if isinstance(if_match, str) and self.objects.get(key, (b"", ""))[1] != if_match:
            raise _client_error("PreconditionFailed")
        self.objects.pop(key, None)
        return {}

    def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
        prefix = kwargs.get("Prefix")
        if not isinstance(prefix, str):
            raise AssertionError("Prefix is required")
        return {"Contents": [{"Key": key} for key in sorted(self.objects) if key.startswith(prefix)]}


class BlockingStaleArtifactPutClient(FakeS3Client):
    """Pause one writer after its conditional version read for a race test."""

    def __init__(self) -> None:
        super().__init__()
        self.stale_put_started = threading.Event()
        self.release_stale_put = threading.Event()

    def put_object(self, **kwargs: object) -> dict[str, object]:
        body = kwargs.get("Body")
        if body == b"a\nstale\n":
            self.stale_put_started.set()
            assert self.release_stale_put.wait(timeout=5)
        return super().put_object(**kwargs)


class DeletedBeforeConditionalArtifactPutClient(FakeS3Client):
    """Simulate S3 deleting an artifact after HEAD but before conditional PUT."""

    def put_object(self, **kwargs: object) -> dict[str, object]:
        if kwargs.get("Body") == b"a\nreplacement\n" and isinstance(kwargs.get("IfMatch"), str):
            self.objects.pop(_key(kwargs), None)
            raise _client_error("NoSuchKey")
        return super().put_object(**kwargs)


def test_s3_workflow_json_uses_owner_and_versions() -> None:
    # Given: Alice owns a workflow in S3 storage
    client = FakeS3Client()
    storage = S3WorkflowStorage(bucket="bucket", prefix="app", client=client)
    alice = UserContext(user_id="alice")
    bob = UserContext(user_id="bob")
    workflow = storage.create_workflow(alice, dataset_workflow_id())

    assert storage.read_json(alice, workflow.dataset_workflow_id, WorkflowFile.WORKFLOW_STATE) is None

    # When: Alice writes and updates mutable workflow state
    first = storage.write_json(alice, workflow.dataset_workflow_id, WorkflowFile.WORKFLOW_STATE, {"stage": "uploaded"})
    second = storage.write_json(
        alice,
        workflow.dataset_workflow_id,
        WorkflowFile.WORKFLOW_STATE,
        {"stage": "mapped"},
        expected_version=first.version,
    )

    # Then: stale writes and another user's reads are rejected
    assert second.data == {"stage": "mapped"}
    with pytest.raises(WorkflowConflictError):
        storage.write_json(
            alice,
            workflow.dataset_workflow_id,
            WorkflowFile.WORKFLOW_STATE,
            {"stage": "harmonized"},
            expected_version=first.version,
        )
    with pytest.raises(WorkflowAccessDeniedError):
        storage.read_json(bob, workflow.dataset_workflow_id, WorkflowFile.WORKFLOW_STATE)


def test_s3_workflow_delete_removes_all_owned_objects() -> None:
    # Given: Alice owns an S3 workflow with durable state.
    client = FakeS3Client()
    storage = S3WorkflowStorage(bucket="bucket", prefix="app", client=client)
    alice = UserContext(user_id="alice")
    workflow = storage.create_workflow(alice, dataset_workflow_id())
    storage.write_json(
        alice,
        workflow.dataset_workflow_id,
        WorkflowFile.WORKFLOW_STATE,
        {"stage": "uploaded"},
    )

    # When: Alice deletes the whole workflow.
    storage.delete_workflow(alice, workflow.dataset_workflow_id)

    # Then: no object under that exact workflow prefix remains.
    assert not any(
        key.startswith("app/workflows/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/")
        for key in client.objects
    )
    with pytest.raises(WorkflowNotFoundError):
        storage.read_json(
            alice,
            workflow.dataset_workflow_id,
            WorkflowFile.WORKFLOW_STATE,
        )


@pytest.mark.parametrize("invalid_content", [b"{", b"\xff"])
def test_s3_workflow_json_reports_invalid_bytes(invalid_content: bytes) -> None:
    # Given: Durable S3 JSON exists but is not valid UTF-8 JSON.
    client = FakeS3Client()
    storage = S3WorkflowStorage(bucket="bucket", prefix="app", client=client)
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    storage.write_json(
        user,
        workflow.dataset_workflow_id,
        WorkflowFile.WORKFLOW_STATE,
        {"stage": "uploaded"},
    )
    key = "app/workflows/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/json/workflow_state.json"
    client.objects[key] = (invalid_content, _etag(invalid_content))

    # When / Then: The storage boundary reports the same typed unreadable error.
    with pytest.raises(WorkflowJsonUnreadableError, match="workflow_state"):
        storage.read_json(user, workflow.dataset_workflow_id, WorkflowFile.WORKFLOW_STATE)


def test_s3_workflow_reports_invalid_metadata_schema() -> None:
    # Given: Existing workflow metadata is valid JSON with an invalid schema.
    client = FakeS3Client()
    storage = S3WorkflowStorage(bucket="bucket", prefix="app", client=client)
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    key = "app/workflows/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/metadata.json"
    client.objects[key] = (b"{}", _etag(b"{}"))

    # When / Then: Existing corrupt metadata has the same typed recovery path.
    with pytest.raises(WorkflowJsonUnreadableError, match="metadata"):
        storage.read_json(user, workflow.dataset_workflow_id, WorkflowFile.WORKFLOW_STATE)


def test_s3_workflow_rejects_metadata_for_another_workflow() -> None:
    # Given: the metadata object for one workflow contains another workflow's identity.
    client = FakeS3Client()
    storage = S3WorkflowStorage(bucket="bucket", prefix="app", client=client)
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    key = "app/workflows/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/metadata.json"
    payload = {
        "file_id": "b" * 32,
        "owner_user_id": user.user_id,
        "created_at": workflow.created_at.isoformat(),
        "last_accessed_at": workflow.last_accessed_at.isoformat(),
        "storage_schema_version": 1,
    }
    body = json.dumps(payload).encode("utf-8")
    client.objects[key] = (body, _etag(body))

    # When / Then: access to the requested workflow reports unreadable metadata.
    with pytest.raises(WorkflowJsonUnreadableError, match="metadata"):
        storage.read_json(user, workflow.dataset_workflow_id, WorkflowFile.WORKFLOW_STATE)


def test_s3_json_delete_preserves_a_concurrent_replacement() -> None:
    # Given: A JSON record changes after delete reads its current version.
    client = FakeS3Client()
    storage = S3WorkflowStorage(bucket="bucket", prefix="app", client=client)
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    storage.write_json(
        user,
        workflow.dataset_workflow_id,
        WorkflowFile.REVIEW_OVERRIDES,
        {"review": "old"},
    )
    key = "app/workflows/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/json/review_overrides.json"
    replacement = b'{"review": "new"}'
    client.replacement_before_delete = (key, replacement)

    # When: The conditional delete observes the concurrent replacement.
    with pytest.raises(WorkflowConflictError):
        storage.delete_json(user, workflow.dataset_workflow_id, WorkflowFile.REVIEW_OVERRIDES)

    # Then: The newer record remains available.
    stored = storage.read_json(user, workflow.dataset_workflow_id, WorkflowFile.REVIEW_OVERRIDES)
    assert stored is not None
    assert stored.data == {"review": "new"}


def test_s3_json_delete_requires_latest_version() -> None:
    # Given: a mutable JSON artifact has been replaced after a worker read it.
    client = FakeS3Client()
    storage = S3WorkflowStorage(bucket="bucket", prefix="app", client=client)
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    first = storage.write_json(
        user,
        workflow.dataset_workflow_id,
        WorkflowFile.REVIEW_OVERRIDES,
        {"review": "old"},
    )
    current = storage.write_json(
        user,
        workflow.dataset_workflow_id,
        WorkflowFile.REVIEW_OVERRIDES,
        {"review": "new"},
        expected_version=first.version,
    )

    # When: the stale worker attempts conditional cleanup.
    with pytest.raises(WorkflowConflictError, match="version changed"):
        storage.delete_json(
            user,
            workflow.dataset_workflow_id,
            WorkflowFile.REVIEW_OVERRIDES,
            expected_version=first.version,
        )

    # Then: the current JSON remains available.
    stored = storage.read_json(user, workflow.dataset_workflow_id, WorkflowFile.REVIEW_OVERRIDES)
    assert stored is not None
    assert stored.version == current.version
    assert stored.data == {"review": "new"}


def test_s3_workflow_artifact_materializes_to_temp_file(tmp_path: Path) -> None:
    # Given: an S3-backed workflow and a local source file
    client = FakeS3Client()
    storage = S3WorkflowStorage(bucket="bucket", prefix="app", client=client)
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    source = tmp_path / "sample.csv"
    source.write_text("a,b\n1,2\n", encoding="utf-8")

    assert not any(key.endswith("original_upload.csv") for key in client.objects)

    # When: the artifact is saved and materialized
    artifact = storage.create_artifact(user, workflow.dataset_workflow_id, WorkflowFile.ORIGINAL_UPLOAD, source)

    # Then: callers get a temporary local path containing the object bytes
    assert artifact.suffix == ".csv"
    with storage.materialize_artifact(user, workflow.dataset_workflow_id, WorkflowFile.ORIGINAL_UPLOAD) as path:
        assert path.exists()
        assert path.suffix == ".csv"
        assert path.read_text(encoding="utf-8") == "a,b\n1,2\n"
    assert not path.exists()


def test_s3_workflow_write_artifact_replaces_existing_object(tmp_path: Path) -> None:
    # Given: a generated artifact already exists in S3
    client = FakeS3Client()
    storage = S3WorkflowStorage(bucket="bucket", prefix="app", client=client)
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    first.write_text("a\nold\n", encoding="utf-8")
    second.write_text("a\nnew\n", encoding="utf-8")
    stored = storage.write_artifact(
        user,
        workflow.dataset_workflow_id,
        WorkflowFile.HARMONIZED_OUTPUT,
        first,
    )

    assert any(key.endswith("harmonized_output.csv") for key in client.objects)

    # When: the generated artifact is written again
    storage.write_artifact(
        user,
        workflow.dataset_workflow_id,
        WorkflowFile.HARMONIZED_OUTPUT,
        second,
        expected_version=stored.version,
    )

    # Then: materialization returns the newest bytes
    with storage.materialize_artifact(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT) as path:
        assert path.read_text(encoding="utf-8") == "a\nnew\n"
    assert storage.artifact_version(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT) is not None


def test_s3_artifact_version_is_none_before_publish() -> None:
    # Given: an authorized workflow with no generated artifact.
    client = FakeS3Client()
    storage = S3WorkflowStorage(bucket="bucket", prefix="app", client=client)
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())

    # When / Then: the read-only publish snapshot reports no artifact.
    assert storage.artifact_version(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT) is None


def test_s3_mutable_artifact_rejects_stale_concurrent_writer(tmp_path: Path) -> None:
    # Given: a worker has read the current artifact version before another worker publishes.
    client = BlockingStaleArtifactPutClient()
    storage = S3WorkflowStorage(bucket="bucket", prefix="app", client=client)
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    initial = tmp_path / "initial.csv"
    newer = tmp_path / "newer.csv"
    stale = tmp_path / "stale.csv"
    initial.write_text("a\nold\n", encoding="utf-8")
    newer.write_text("a\nnewer\n", encoding="utf-8")
    stale.write_text("a\nstale\n", encoding="utf-8")
    initial_artifact = storage.write_artifact(
        user,
        workflow.dataset_workflow_id,
        WorkflowFile.HARMONIZED_OUTPUT,
        initial,
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        stale_future = executor.submit(
            storage.write_artifact,
            user,
            workflow.dataset_workflow_id,
            WorkflowFile.HARMONIZED_OUTPUT,
            stale,
            initial_artifact.version,
        )
        assert client.stale_put_started.wait(timeout=5)

        # When: the newer worker publishes while the stale put is waiting.
        storage.write_artifact(
            user,
            workflow.dataset_workflow_id,
            WorkflowFile.HARMONIZED_OUTPUT,
            newer,
            initial_artifact.version,
        )
        client.release_stale_put.set()

        # Then: S3 rejects the stale conditional put and the newer output remains.
        with pytest.raises(WorkflowConflictError, match="already exists|version changed"):
            stale_future.result(timeout=5)
    with storage.materialize_artifact(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT) as path:
        assert path.read_text(encoding="utf-8") == "a\nnewer\n"


def test_s3_mutable_artifact_maps_a_conditional_delete_race_to_conflict(tmp_path: Path) -> None:
    client = DeletedBeforeConditionalArtifactPutClient()
    storage = S3WorkflowStorage(bucket="bucket", prefix="app", client=client)
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    initial = tmp_path / "initial.csv"
    replacement = tmp_path / "replacement.csv"
    initial.write_text("a\nold\n", encoding="utf-8")
    replacement.write_text("a\nreplacement\n", encoding="utf-8")
    stored = storage.write_artifact(
        user,
        workflow.dataset_workflow_id,
        WorkflowFile.HARMONIZED_OUTPUT,
        initial,
    )

    with pytest.raises(WorkflowConflictError, match="version changed"):
        storage.write_artifact(
            user,
            workflow.dataset_workflow_id,
            WorkflowFile.HARMONIZED_OUTPUT,
            replacement,
            expected_version=stored.version,
        )


def test_s3_workflow_artifact_rejects_suffix_change_and_similar_prefix(tmp_path: Path) -> None:
    client = FakeS3Client()
    storage = S3WorkflowStorage(bucket="bucket", prefix="app", client=client)
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    current = tmp_path / "current.csv"
    different = tmp_path / "different.tsv"
    current.write_text("a\ncurrent\n", encoding="utf-8")
    different.write_text("a\trejected\n", encoding="utf-8")
    storage.write_artifact(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT, current)
    similar_key = "app/workflows/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/artifacts/harmonized_output_backup.csv"
    client.objects[similar_key] = (b"a\nstale\n", _etag(b"a\nstale\n"))

    with pytest.raises(WorkflowConflictError, match="suffix"):
        storage.write_artifact(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT, different)

    with storage.materialize_artifact(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT) as path:
        assert path.read_text(encoding="utf-8") == "a\ncurrent\n"


def test_s3_original_upload_is_create_once_across_suffixes_and_write(tmp_path: Path) -> None:
    client = FakeS3Client()
    storage = S3WorkflowStorage(bucket="bucket", prefix="app", client=client)
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    original = tmp_path / "original.csv"
    different = tmp_path / "different.tsv"
    replacement = tmp_path / "replacement.csv"
    original.write_text("a\nold\n", encoding="utf-8")
    different.write_text("a\tother\n", encoding="utf-8")
    replacement.write_text("a\nnew\n", encoding="utf-8")
    storage.create_artifact(user, workflow.dataset_workflow_id, WorkflowFile.ORIGINAL_UPLOAD, original)

    with pytest.raises(WorkflowConflictError, match="already exists"):
        storage.create_artifact(user, workflow.dataset_workflow_id, WorkflowFile.ORIGINAL_UPLOAD, different)
    with pytest.raises(WorkflowConflictError, match="create-once"):
        storage.write_artifact(user, workflow.dataset_workflow_id, WorkflowFile.ORIGINAL_UPLOAD, replacement)

    with storage.materialize_artifact(user, workflow.dataset_workflow_id, WorkflowFile.ORIGINAL_UPLOAD) as path:
        assert path.read_text(encoding="utf-8") == "a\nold\n"


def test_s3_artifact_rejects_multiple_exact_variants(tmp_path: Path) -> None:
    client = FakeS3Client()
    storage = S3WorkflowStorage(bucket="bucket", prefix="app", client=client)
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    csv_key = "app/workflows/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/artifacts/harmonized_output.csv"
    tsv_key = "app/workflows/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/artifacts/harmonized_output.tsv"
    client.objects[csv_key] = (b"csv", _etag(b"csv"))
    client.objects[tsv_key] = (b"tsv", _etag(b"tsv"))

    with pytest.raises(WorkflowConflictError, match="multiple"):
        with storage.materialize_artifact(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT):
            pass


def test_failed_s3_artifact_replacement_preserves_last_complete_object(tmp_path: Path) -> None:
    client = FakeS3Client()
    storage = S3WorkflowStorage(bucket="bucket", prefix="app", client=client)
    user = UserContext(user_id="alice")
    workflow = storage.create_workflow(user, dataset_workflow_id())
    previous = tmp_path / "previous.csv"
    replacement = tmp_path / "replacement.csv"
    previous.write_text("a\nold\n", encoding="utf-8")
    replacement.write_text("a\tnew\n", encoding="utf-8")
    stored = storage.write_artifact(
        user,
        workflow.dataset_workflow_id,
        WorkflowFile.HARMONIZED_OUTPUT,
        previous,
    )
    client.failed_put_key = "app/workflows/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/artifacts/harmonized_output.csv"

    with pytest.raises(ClientError):
        storage.write_artifact(
            user,
            workflow.dataset_workflow_id,
            WorkflowFile.HARMONIZED_OUTPUT,
            replacement,
            expected_version=stored.version,
        )

    with storage.materialize_artifact(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT) as path:
        assert path.suffix == ".csv"
        assert path.read_text(encoding="utf-8") == "a\nold\n"

    client.failed_put_key = None
    storage.write_artifact(
        user,
        workflow.dataset_workflow_id,
        WorkflowFile.HARMONIZED_OUTPUT,
        replacement,
        expected_version=stored.version,
    )
    with storage.materialize_artifact(user, workflow.dataset_workflow_id, WorkflowFile.HARMONIZED_OUTPUT) as path:
        assert path.read_text(encoding="utf-8") == "a\tnew\n"


def _key(kwargs: dict[str, object]) -> str:
    key = kwargs.get("Key")
    if not isinstance(key, str):
        raise AssertionError("Key is required")
    return key


def _etag(body: bytes) -> str:
    return f'"{hashlib.md5(body, usedforsecurity=False).hexdigest()}"'


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code}}, "FakeOperation")
