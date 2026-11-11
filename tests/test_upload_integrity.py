"""Browser uploads must match independently supplied file fingerprints."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from httpx import AsyncClient

from src.storage import UploadConstraints, UploadStorage, UploadStream


def _fields(content: bytes) -> dict[str, str]:
    return {
        "expected_sha256": hashlib.sha256(content).hexdigest(),
        "expected_size_bytes": str(len(content)),
    }


@pytest.mark.parametrize("changed", ["checksum", "size", "truncated"])
async def test_mismatching_upload_creates_no_workflow(
    app_client: AsyncClient, temp_storage: UploadStorage, changed: str,
) -> None:
    # Given an intact source file and an independent browser fingerprint.
    content = b"id,value\n1,alpha\n2,beta\n"
    fields = _fields(content)
    if changed == "checksum":
        content = content.replace(b"alpha", b"ALPHA")
    elif changed == "size":
        fields["expected_size_bytes"] = str(len(content) + 1)
    else:
        content = b"id,value\n1,alpha\n"

    # When changed bytes or a different byte count arrive at the upload endpoint.
    response = await app_client.post(
        "/stage-1/upload", data=fields,
        files={"file": ("sample.csv", content, "text/csv")},
    )

    # Then verification fails before any upload metadata or workflow is published.
    assert response.status_code == 400
    assert response.json() == {"detail": "The uploaded file did not match the selected file. Please try again."}
    assert list(temp_storage._data_dir.iterdir()) == []
    assert list(temp_storage._meta_dir.iterdir()) == []
    assert list((temp_storage._base_dir / "workflow_storage" / "workflows").iterdir()) == []


@pytest.mark.parametrize("invalid", ["missing", "hash", "negative", "fraction"])
async def test_upload_requires_well_formed_fingerprint(
    app_client: AsyncClient, temp_storage: UploadStorage, invalid: str,
) -> None:
    # Given a valid file but absent or malformed verification fields.
    content = b"value\nalpha\n"
    fields = _fields(content)
    if invalid == "missing":
        fields = {}
    elif invalid == "hash":
        fields["expected_sha256"] = "not-a-hash"
    elif invalid == "negative":
        fields["expected_size_bytes"] = "-1"
    else:
        fields["expected_size_bytes"] = "1.5"

    # When the malformed request is submitted.
    response = await app_client.post(
        "/stage-1/upload", data=fields,
        files={"file": ("sample.csv", content, "text/csv")},
    )

    # Then it fails at the request boundary without storing a file.
    assert response.status_code == 422
    assert list(temp_storage._data_dir.iterdir()) == []
    assert list(temp_storage._meta_dir.iterdir()) == []


async def test_verified_upload_retains_receipt_and_exact_bytes(
    app_client: AsyncClient, temp_storage: UploadStorage,
) -> None:
    # Given binary-exact source bytes with a BOM, non-ASCII text, and CRLF endings.
    content = b"\xef\xbb\xbfid,value\r\n1,caf\xc3\xa9\r\n"
    fields = _fields(content)
    fields["expected_sha256"] = fields["expected_sha256"].upper()

    # When the browser sends the matching checksum and file size.
    response = await app_client.post(
        "/stage-1/upload", data=fields,
        files={"file": ("source.csv", content, "text/csv")},
    )

    # Then the saved bytes and reloaded verification receipt match the original file.
    assert response.status_code == 201, response.text
    expected = {"sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)}
    assert response.json()["verification"] == expected
    meta = temp_storage.load(response.json()["file_id"])
    assert meta is not None
    assert meta.saved_path.read_bytes() == content
    assert meta.verification is not None
    assert meta.verification.sha256 == expected["sha256"]
    assert meta.verification.size_bytes == len(content)


async def test_verification_reads_saved_file_not_just_input_stream(
    app_client: AsyncClient, temp_storage: UploadStorage, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a storage writer that returns the correct count but changes saved bytes.
    content = b"value\nalpha\n"
    real_write = temp_storage._write_upload_chunks

    async def _write_changed(upload: UploadStream, destination: Path) -> int:
        count = await real_write(upload, destination)
        destination.write_bytes(content.replace(b"alpha", b"ALPHA"))
        return count

    monkeypatch.setattr(temp_storage, "_write_upload_chunks", _write_changed)

    # When the completed scratch file is checked against the original fingerprint.
    response = await app_client.post(
        "/stage-1/upload", data=_fields(content),
        files={"file": ("sample.csv", content, "text/csv")},
    )

    # Then the changed saved bytes are rejected and cleaned up.
    assert response.status_code == 400
    assert list(temp_storage._data_dir.iterdir()) == []
    assert list(temp_storage._meta_dir.iterdir()) == []


async def test_actual_size_limit_is_enforced_despite_small_claim(
    app_client: AsyncClient, temp_storage: UploadStorage, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given bytes larger than storage's limit and a falsely small claimed size.
    monkeypatch.setattr(temp_storage, "_constraints", UploadConstraints(max_bytes=1024))
    content = b"x" * 1025
    fields = _fields(content)
    fields["expected_size_bytes"] = "1"

    # When the body is read, independently of the supplied size metadata.
    response = await app_client.post(
        "/stage-1/upload", data=fields,
        files={"file": ("large.csv", content, "text/csv")},
    )

    # Then the real byte limit stops it before workflow publication.
    assert response.status_code == 413
    assert list(temp_storage._data_dir.iterdir()) == []
    assert list(temp_storage._meta_dir.iterdir()) == []
    assert list((temp_storage._base_dir / "workflow_storage" / "workflows").iterdir()) == []
