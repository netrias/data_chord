"""Scratch upload failure behavior."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.domain.dataset_workflow_ids import dataset_workflow_id_from_string
from src.storage import UploadConstraints, UploadedFileMeta, UploadStorage


class MockFailingUpload:
    def __init__(self) -> None:
        self.filename: str | None = "sample.csv"
        self.content_type: str | None = "text/csv"
        self._read_count = 0
        self.closed = False

    async def read(self, size: int = -1) -> bytes:
        del size
        self._read_count += 1
        if self._read_count == 1:
            return b"value\n"
        raise OSError("disk full")

    async def close(self) -> None:
        self.closed = True


class MockSuccessfulUpload:
    def __init__(self) -> None:
        self.filename: str | None = "sample.csv"
        self.content_type: str | None = "text/csv"
        self._complete = False
        self.closed = False

    async def read(self, size: int = -1) -> bytes:
        del size
        if self._complete:
            return b""
        self._complete = True
        return b"value\n1\n"

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_upload_write_error_removes_partial_scratch_file(tmp_path: Path) -> None:
    # Given: an upload writes one chunk before its stream reports a disk error.
    storage = UploadStorage(tmp_path / "scratch", UploadConstraints(max_bytes=1024))
    mock_upload = MockFailingUpload()

    # When: scratch storage accepts the upload.
    with pytest.raises(OSError, match="disk full"):
        await storage.store(mock_upload, dataset_workflow_id_from_string("a" * 32))

    # Then: the partial file is removed and the upload stream is closed.
    assert list((tmp_path / "scratch" / "files").iterdir()) == []
    assert mock_upload.closed is True


@pytest.mark.asyncio
async def test_cancelled_upload_removes_only_its_own_partial_file(tmp_path: Path) -> None:
    # Given another file exists and this upload is cancelled after its first chunk.
    storage = UploadStorage(tmp_path / "scratch", UploadConstraints(max_bytes=1024))
    other = storage._data_dir / "other.csv"
    other.write_bytes(b"value\nkeep\n")

    class MockCancelledUpload(MockFailingUpload):
        async def read(self, size: int = -1) -> bytes:
            if self._read_count == 0:
                return await super().read(size)
            raise asyncio.CancelledError

    upload = MockCancelledUpload()

    # When cancellation interrupts the scratch write.
    with pytest.raises(asyncio.CancelledError):
        await storage.store(upload, dataset_workflow_id_from_string("a" * 32))

    # Then the partial upload is gone, the stream is closed, and the other file survives.
    assert list(storage._data_dir.iterdir()) == [other]
    assert other.read_bytes() == b"value\nkeep\n"
    assert list(storage._meta_dir.iterdir()) == []
    assert upload.closed is True


@pytest.mark.asyncio
async def test_server_generated_upload_can_replace_its_fixed_demo_identity(tmp_path: Path) -> None:
    # Given a previous packaged demo upload at its fixed identity.
    storage = UploadStorage(tmp_path / "scratch", UploadConstraints(max_bytes=1024))
    file_id = dataset_workflow_id_from_string("a" * 32)
    existing = storage._data_dir / f"{file_id}.csv"
    existing.write_bytes(b"value\nkeep\n")
    upload = MockSuccessfulUpload()

    # When demo bootstrap prepares the same identity again without browser expectations.
    await storage.store(upload, file_id)

    # Then the internal replacement contract still works and closes the stream.
    assert existing.read_bytes() == b"value\n1\n"
    assert upload.closed is True


@pytest.mark.asyncio
async def test_close_failure_removes_completed_upload_before_metadata(tmp_path: Path) -> None:
    # Given a stream that writes successfully but fails when it is closed.
    storage = UploadStorage(tmp_path / "scratch", UploadConstraints(max_bytes=1024))

    class MockCloseFailure(MockSuccessfulUpload):
        async def close(self) -> None:
            raise OSError("close failed")

    # When storage finishes reading and closes the stream.
    with pytest.raises(OSError, match="close failed"):
        await storage.store(MockCloseFailure(), dataset_workflow_id_from_string("a" * 32))

    # Then no completed file or metadata can be published.
    assert list(storage._data_dir.iterdir()) == []
    assert list(storage._meta_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_server_generated_upload_has_no_browser_verification_receipt(tmp_path: Path) -> None:
    # Given an internally generated file with no browser-supplied fingerprint.
    storage = UploadStorage(tmp_path / "scratch", UploadConstraints(max_bytes=1024))
    file_id = dataset_workflow_id_from_string("a" * 32)

    # When it is stored and then loaded using the existing metadata contract.
    meta = await storage.store(MockSuccessfulUpload(), file_id)
    reloaded = storage.load(file_id)

    # Then it remains readable without inventing a client-verification claim.
    assert meta.verification is None
    assert reloaded is not None
    assert reloaded.verification is None
    assert storage.metadata_payload(meta) == {
        "file_id": file_id, "original_name": "sample.csv", "content_type": "text/csv",
        "size_bytes": 8, "saved_name": f"{file_id}.csv", "uploaded_at": meta.uploaded_at.isoformat(),
        "tabular_format": "csv", "sheet_names": [], "selected_sheet": None,
    }


@pytest.mark.asyncio
async def test_metadata_write_error_removes_completed_scratch_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: scratch bytes finish writing, but metadata storage reports a disk error.
    storage = UploadStorage(tmp_path / "scratch", UploadConstraints(max_bytes=1024))
    mock_upload = MockSuccessfulUpload()

    def _fail_metadata(_meta: UploadedFileMeta) -> None:
        raise OSError("metadata disk full")

    monkeypatch.setattr(storage, "_write_metadata", _fail_metadata)

    # When: scratch storage completes the upload.
    with pytest.raises(OSError, match="metadata disk full"):
        await storage.store(mock_upload, dataset_workflow_id_from_string("b" * 32))

    # Then: neither the completed file nor partial metadata remains orphaned.
    assert list((tmp_path / "scratch" / "files").iterdir()) == []
    assert list((tmp_path / "scratch" / "meta").iterdir()) == []
    assert mock_upload.closed is True
