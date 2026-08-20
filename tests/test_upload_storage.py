"""Scratch upload failure behavior."""

from __future__ import annotations

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
