"""
Persist uploaded files and expose their metadata.

Handle file storage, metadata tracking, and manifest management for the
harmonization workflow.
"""

from __future__ import annotations

import csv
import json
import logging
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict, cast
from uuid import uuid4

from fastapi import HTTPException, UploadFile

logger = logging.getLogger(__name__)

HARMONIZED_SUFFIX = ".harmonized.csv"


class StoredMeta(TypedDict):
    """why: describe the JSON payload that mirrors uploads on disk."""

    file_id: str
    original_name: str
    content_type: str
    size_bytes: int
    saved_name: str
    uploaded_at: str


@dataclass(frozen=True)
class UploadConstraints:
    """why: capture allowed file characteristics for uploads."""

    allowed_suffixes: tuple[str, ...]
    allowed_content_types: tuple[str, ...]
    max_bytes: int
    chunk_size: int = 1024 * 1024


@dataclass(frozen=True)
class UploadedFileMeta:
    """why: describe where an uploaded file lives on disk."""

    file_id: str
    original_name: str
    content_type: str
    size_bytes: int
    saved_path: Path
    uploaded_at: datetime

    @property
    def human_size(self) -> str:
        """why: convert byte counts into a UI-friendly message."""
        size = float(self.size_bytes)
        units = ["B", "KB", "MB", "GB"]
        for unit in units:
            if size < 1024 or unit == units[-1]:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{self.size_bytes} B"


class UploadError(RuntimeError):
    """why: represent upload-specific failures."""


class UnsupportedUploadError(UploadError):
    """why: signal when the file type is not allowed."""


class UploadTooLargeError(UploadError):
    """why: indicate the payload exceeded configured limits."""


class UploadStorage:
    """why: persist uploaded files and expose their metadata."""

    def __init__(self, base_dir: Path, constraints: UploadConstraints) -> None:
        self._base_dir: Path = base_dir
        self._data_dir: Path = base_dir / "files"
        self._meta_dir: Path = base_dir / "meta"
        self._constraints: UploadConstraints = constraints
        self._manifest_dir: Path = base_dir / "manifests"
        self._ensure_workspace()

    def _ensure_workspace(self) -> None:
        """why: make sure upload directories are ready."""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._meta_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_dir.mkdir(parents=True, exist_ok=True)

    async def store(self, upload: UploadFile) -> UploadedFileMeta:
        """why: validate and persist the upload stream."""
        filename, suffix, content_type = self._extract_upload_info(upload)
        self._validate_upload(suffix, content_type)

        file_id = uuid4().hex
        destination = self._data_dir / f"{file_id}{suffix}"

        try:
            total_bytes = await self._write_upload_chunks(upload, destination)
        finally:
            await upload.close()

        return self._create_and_save_metadata(file_id, filename, content_type, total_bytes, destination)

    def _extract_upload_info(self, upload: UploadFile) -> tuple[str, str, str]:
        """why: normalize upload metadata with sensible defaults."""
        filename = upload.filename or "dataset.csv"
        suffix = Path(filename).suffix.lower() or ".csv"
        content_type = (upload.content_type or "text/csv").lower()
        return filename, suffix, content_type

    async def _write_upload_chunks(self, upload: UploadFile, destination: Path) -> int:
        """why: stream upload to disk with size validation."""
        total_bytes = 0
        try:
            with destination.open("wb") as target:
                while chunk := await upload.read(self._constraints.chunk_size):
                    total_bytes += len(chunk)
                    if total_bytes > self._constraints.max_bytes:
                        raise UploadTooLargeError(f"File exceeds {self._constraints.max_bytes} bytes limit")
                    target.write(chunk)
        except UploadTooLargeError:
            destination.unlink(missing_ok=True)
            logger.warning("Upload aborted because it was too large", extra={"destination": str(destination)})
            raise
        return total_bytes

    def _create_and_save_metadata(
        self,
        file_id: str,
        filename: str,
        content_type: str,
        total_bytes: int,
        destination: Path,
    ) -> UploadedFileMeta:
        """why: build metadata object and persist to disk."""
        meta = UploadedFileMeta(
            file_id=file_id,
            original_name=filename,
            content_type=content_type,
            size_bytes=total_bytes,
            saved_path=destination,
            uploaded_at=datetime.now(UTC),
        )
        self._write_metadata(meta)
        logger.info("Stored upload", extra={"file_id": file_id, "size_bytes": total_bytes})
        return meta

    def load(self, file_id: str) -> UploadedFileMeta | None:
        """why: reconstruct metadata for a previously stored upload."""
        meta_path = self._meta_dir / f"{file_id}.json"
        if not meta_path.exists():
            return None

        payload = cast(StoredMeta, json.loads(meta_path.read_text()))
        return UploadedFileMeta(
            file_id=file_id,
            original_name=payload["original_name"],
            content_type=payload["content_type"],
            size_bytes=payload["size_bytes"],
            saved_path=self._data_dir / payload["saved_name"],
            uploaded_at=datetime.fromisoformat(payload["uploaded_at"]),
        )

    def _write_metadata(self, meta: UploadedFileMeta) -> None:
        """why: persist metadata alongside the file."""
        meta_payload = {
            "file_id": meta.file_id,
            "original_name": meta.original_name,
            "content_type": meta.content_type,
            "size_bytes": meta.size_bytes,
            "saved_name": meta.saved_path.name,
            "uploaded_at": meta.uploaded_at.isoformat(),
        }
        meta_path = self._meta_dir / f"{meta.file_id}.json"
        meta_path.write_text(json.dumps(meta_payload, indent=2))

    def save_manifest(self, file_id: str, manifest: Mapping[str, object]) -> Path:
        """why: persist the harmonization manifest for reuse across stages."""
        path = self._manifest_dir / f"{file_id}.json"
        path.write_text(json.dumps(manifest, indent=2))
        logger.info("Stored manifest", extra={"file_id": file_id, "manifest_path": str(path)})
        return path

    def load_manifest(self, file_id: str) -> Mapping[str, object] | None:
        """why: retrieve a previously stored manifest."""
        path = self._manifest_dir / f"{file_id}.json"
        if not path.exists():
            return None
        try:
            return cast(Mapping[str, object], json.loads(path.read_text()))
        except json.JSONDecodeError:
            logger.warning("Manifest file corrupt", extra={"file_id": file_id, "path": str(path)})
            return None

    def save_harmonization_manifest(self, file_id: str, manifest_path: Path) -> Path:
        """why: copy the parquet manifest to storage for cross-stage access."""
        destination = self._manifest_dir / f"{file_id}_harmonization.parquet"
        shutil.copy2(manifest_path, destination)
        logger.info("Stored harmonization manifest", extra={"file_id": file_id, "path": str(destination)})
        return destination

    def load_harmonization_manifest_path(self, file_id: str) -> Path | None:
        """why: retrieve the stored harmonization manifest path."""
        path = self._manifest_dir / f"{file_id}_harmonization.parquet"
        return path if path.exists() else None

    @property
    def manifest_dir(self) -> Path:
        """why: expose manifest directory for tests and direct access scenarios."""
        return self._manifest_dir

    def _validate_upload(self, suffix: str, content_type: str) -> None:
        """why: guard against unsupported file types."""
        if suffix not in self._constraints.allowed_suffixes:
            raise UnsupportedUploadError(f"Unsupported file extension: {suffix}")
        if content_type not in self._constraints.allowed_content_types:
            raise UnsupportedUploadError(f"Unsupported content type: {content_type}")


def describe_constraints(constraints: UploadConstraints) -> dict[str, str | int]:
    """why: present constraint information to the UI layer."""
    max_mb = constraints.max_bytes / (1024 * 1024)
    return {
        "max_mb": f"{max_mb:.0f}",
        "allowed_types": ", ".join(sorted(constraints.allowed_suffixes)),
        "max_bytes": constraints.max_bytes,
    }


def resolve_harmonized_path(original_path: Path, file_id: str) -> Path | None:
    """why: locate harmonized CSV using multiple naming conventions."""
    candidates = [
        original_path.with_name(f"{original_path.stem}{HARMONIZED_SUFFIX}"),
        original_path.with_suffix(original_path.suffix + HARMONIZED_SUFFIX),
        Path.cwd() / f"{file_id}{HARMONIZED_SUFFIX}",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


_ERROR_HARMONIZED_NOT_FOUND = "Harmonized file not found. Please rerun Stage 3."
_ERROR_DATASET_NOT_FOUND = "Required dataset file not found."


def resolve_harmonized_path_or_404(original_path: Path, file_id: str) -> Path:
    """why: locate harmonized CSV or raise HTTP 404."""
    path = resolve_harmonized_path(original_path, file_id)
    if path is None:
        raise HTTPException(status_code=404, detail=_ERROR_HARMONIZED_NOT_FOUND)
    return path


def load_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """why: read CSV into headers and row dictionaries.

    Raises HTTPException 404 if file does not exist.
    """
    if not path.exists():
        raise HTTPException(status_code=404, detail=_ERROR_DATASET_NOT_FOUND)
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        headers = list(reader.fieldnames) if reader.fieldnames else []
    return headers, rows


__all__ = [
    "HARMONIZED_SUFFIX",
    "StoredMeta",
    "UploadConstraints",
    "UploadedFileMeta",
    "UploadError",
    "UnsupportedUploadError",
    "UploadTooLargeError",
    "UploadStorage",
    "describe_constraints",
    "load_csv",
    "resolve_harmonized_path",
    "resolve_harmonized_path_or_404",
]
