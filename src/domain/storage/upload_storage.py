"""
Manage uploaded file storage, metadata persistence, and manifest inventory.

Orchestrates file I/O, metadata tracking, and directory organization.
"""

from __future__ import annotations

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
from netrias_client import (
    SUPPORTED_TABULAR_SUFFIXES,
    get_tabular_format,
    is_supported_tabular_content_type,
)

from src.domain.manifest import ManifestPayload, normalize_manifest

logger = logging.getLogger(__name__)


class StoredMeta(TypedDict):
    file_id: str
    original_name: str
    content_type: str
    size_bytes: int
    saved_name: str
    uploaded_at: str


@dataclass(frozen=True)
class UploadConstraints:
    max_bytes: int
    chunk_size: int = 1024 * 1024


@dataclass(frozen=True)
class UploadedFileMeta:
    file_id: str
    original_name: str
    content_type: str
    size_bytes: int
    saved_path: Path
    uploaded_at: datetime

    @property
    def human_size(self) -> str:
        """Format for UI display with progressive unit scaling."""
        size = float(self.size_bytes)
        units = ["B", "KB", "MB", "GB"]
        for unit in units:
            if size < 1024 or unit == units[-1]:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{self.size_bytes} B"


class UploadError(RuntimeError):
    pass


class UnsupportedUploadError(UploadError):
    pass


class UploadTooLargeError(UploadError):
    pass


def _remove_temp_file(source: Path, destination: Path) -> None:
    """Clean up temporary SDK outputs after copying them into managed storage."""
    if source.resolve() == destination.resolve():
        return
    try:
        source.unlink()
    except OSError:
        logger.debug("Could not remove temp file", extra={"path": str(source)})


def _harmonized_suffix_for(original_path: Path) -> str:
    return f".harmonized{original_path.suffix.lower() or '.csv'}"


class UploadStorage:
    def __init__(self, base_dir: Path, constraints: UploadConstraints) -> None:
        self._base_dir: Path = base_dir
        self._data_dir: Path = base_dir / "files"
        self._meta_dir: Path = base_dir / "meta"
        self._constraints: UploadConstraints = constraints
        self._manifest_dir: Path = base_dir / "manifests"
        self._harmonized_dir: Path = base_dir / "harmonized"
        self._ensure_workspace()

    def _ensure_workspace(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._meta_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_dir.mkdir(parents=True, exist_ok=True)
        self._harmonized_dir.mkdir(parents=True, exist_ok=True)

    async def store(self, upload: UploadFile) -> UploadedFileMeta:
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
        filename = upload.filename or "dataset.csv"
        suffix = Path(filename).suffix.lower() or ".csv"
        content_type = (upload.content_type or "text/csv").lower()
        return filename, suffix, content_type

    async def _write_upload_chunks(self, upload: UploadFile, destination: Path) -> int:
        """Stream in chunks to avoid loading entire file into memory."""
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

    def save_manifest(self, file_id: str, manifest: ManifestPayload | Mapping[str, object]) -> Path:
        path = self._manifest_dir / f"{file_id}.json"
        path.write_text(json.dumps(normalize_manifest(manifest), indent=2))
        logger.info("Stored manifest", extra={"file_id": file_id, "manifest_path": str(path)})
        return path

    def load_manifest(self, file_id: str) -> ManifestPayload | None:
        path = self._manifest_dir / f"{file_id}.json"
        if not path.exists():
            return None
        try:
            return normalize_manifest(cast(object, json.loads(path.read_text())))
        except json.JSONDecodeError:
            logger.warning("Manifest file corrupt", extra={"file_id": file_id, "path": str(path)})
            return None

    def save_harmonization_manifest(self, file_id: str, manifest_path: Path) -> Path:
        destination = self._manifest_dir / f"{file_id}_harmonization.parquet"
        shutil.copy2(manifest_path, destination)
        _remove_temp_file(manifest_path, destination)
        logger.info("Stored harmonization manifest", extra={"file_id": file_id, "path": str(destination)})
        return destination

    def load_harmonization_manifest_path(self, file_id: str) -> Path | None:
        path = self._manifest_dir / f"{file_id}_harmonization.parquet"
        return path if path.exists() else None

    def harmonized_path_for(self, file_id: str, original_path: Path) -> Path:
        return self._harmonized_dir / f"{file_id}{_harmonized_suffix_for(original_path)}"

    @property
    def harmonized_dir(self) -> Path:
        return self._harmonized_dir

    @property
    def manifest_dir(self) -> Path:
        return self._manifest_dir

    def _validate_upload(self, suffix: str, content_type: str) -> None:
        if suffix not in SUPPORTED_TABULAR_SUFFIXES:
            raise UnsupportedUploadError(f"Unsupported file extension: {suffix}")
        try:
            file_format = get_tabular_format(Path(f"dataset{suffix}"), content_type)
        except ValueError as exc:
            raise UnsupportedUploadError(str(exc)) from exc
        if not is_supported_tabular_content_type(content_type, file_format):
            raise UnsupportedUploadError(f"Unsupported content type for {suffix}: {content_type}")


def describe_constraints(constraints: UploadConstraints) -> dict[str, str | int]:
    max_mb = constraints.max_bytes / (1024 * 1024)
    return {
        "max_mb": f"{max_mb:.0f}",
        "allowed_types": ", ".join(sorted(SUPPORTED_TABULAR_SUFFIXES)),
        "max_bytes": constraints.max_bytes,
    }


def resolve_harmonized_path(original_path: Path, file_id: str) -> Path | None:
    harmonized_dir = original_path.parent.parent / "harmonized"
    candidate = harmonized_dir / f"{file_id}{_harmonized_suffix_for(original_path)}"
    return candidate if candidate.exists() else None


_ERROR_HARMONIZED_NOT_FOUND = "Harmonized file not found. Please rerun Stage 3."


def resolve_harmonized_path_or_404(original_path: Path, file_id: str) -> Path:
    path = resolve_harmonized_path(original_path, file_id)
    if path is None:
        raise HTTPException(status_code=404, detail=_ERROR_HARMONIZED_NOT_FOUND)
    return path


__all__ = [
    "StoredMeta",
    "UploadConstraints",
    "UploadedFileMeta",
    "UploadError",
    "UnsupportedUploadError",
    "UploadTooLargeError",
    "UploadStorage",
    "describe_constraints",
    "resolve_harmonized_path",
    "resolve_harmonized_path_or_404",
]
