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
from typing import Final, NotRequired, TypedDict, cast
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from netrias_client import (
    SUPPORTED_TABULAR_SUFFIXES,
    TabularFormat,
    get_tabular_format,
    is_supported_tabular_content_type,
    list_workbook_sheets,
)

from src.domain.manifest import ManifestPayload, normalize_manifest

logger = logging.getLogger(__name__)

DEFAULT_UPLOAD_FILENAME: Final = "dataset.csv"
DEFAULT_UPLOAD_CONTENT_TYPE: Final = "text/csv"

_META_FILE_ID: Final = "file_id"
_META_ORIGINAL_NAME: Final = "original_name"
_META_CONTENT_TYPE: Final = "content_type"
_META_SIZE_BYTES: Final = "size_bytes"
_META_SAVED_NAME: Final = "saved_name"
_META_UPLOADED_AT: Final = "uploaded_at"
_META_TABULAR_FORMAT: Final = "tabular_format"
_META_SHEET_NAMES: Final = "sheet_names"
_META_SELECTED_SHEET: Final = "selected_sheet"


class StoredMeta(TypedDict):
    """JSON metadata persisted next to the uploaded dataset."""

    file_id: str
    original_name: str
    content_type: str
    size_bytes: int
    saved_name: str
    uploaded_at: str
    tabular_format: NotRequired[str]
    sheet_names: NotRequired[list[str]]
    selected_sheet: NotRequired[str | None]


@dataclass(frozen=True)
class UploadConstraints:
    """Upload limits enforced while streaming incoming files to disk."""

    max_bytes: int
    chunk_size: int = 1024 * 1024


@dataclass(frozen=True)
class UploadedFileMeta:
    """Canonical metadata for one uploaded dataset in managed storage."""

    file_id: str
    original_name: str
    content_type: str
    size_bytes: int
    saved_path: Path
    uploaded_at: datetime
    tabular_format: TabularFormat
    sheet_names: list[str]
    selected_sheet: str | None = None

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
    """Base error for validation or storage failures while accepting uploads."""

    pass


class UnsupportedUploadError(UploadError):
    """Raised when the uploaded file type cannot be parsed as supported tabular data."""

    pass


class UploadTooLargeError(UploadError):
    """Raised as soon as streamed bytes exceed the configured upload limit."""

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

        try:
            return self._create_and_save_metadata(file_id, filename, content_type, total_bytes, destination)
        except UnsupportedUploadError:
            destination.unlink(missing_ok=True)
            raise

    def _extract_upload_info(self, upload: UploadFile) -> tuple[str, str, str]:
        filename = upload.filename or DEFAULT_UPLOAD_FILENAME
        suffix = Path(filename).suffix.lower() or ".csv"
        content_type = (upload.content_type or DEFAULT_UPLOAD_CONTENT_TYPE).lower()
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
        file_format = get_tabular_format(destination, content_type)
        sheet_names = _sheet_names_for(destination)
        selected_sheet = sheet_names[0] if sheet_names else None
        meta = UploadedFileMeta(
            file_id=file_id,
            original_name=filename,
            content_type=content_type,
            size_bytes=total_bytes,
            saved_path=destination,
            uploaded_at=datetime.now(UTC),
            tabular_format=file_format,
            sheet_names=sheet_names,
            selected_sheet=selected_sheet,
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
            original_name=payload[_META_ORIGINAL_NAME],
            content_type=payload[_META_CONTENT_TYPE],
            size_bytes=payload[_META_SIZE_BYTES],
            saved_path=self._data_dir / payload[_META_SAVED_NAME],
            uploaded_at=datetime.fromisoformat(payload[_META_UPLOADED_AT]),
            tabular_format=_tabular_format_from_metadata(payload),
            sheet_names=payload.get(_META_SHEET_NAMES, []),
            selected_sheet=payload.get(_META_SELECTED_SHEET),
        )

    def _write_metadata(self, meta: UploadedFileMeta) -> None:
        meta_payload = {
            _META_FILE_ID: meta.file_id,
            _META_ORIGINAL_NAME: meta.original_name,
            _META_CONTENT_TYPE: meta.content_type,
            _META_SIZE_BYTES: meta.size_bytes,
            _META_SAVED_NAME: meta.saved_path.name,
            _META_UPLOADED_AT: meta.uploaded_at.isoformat(),
            _META_TABULAR_FORMAT: meta.tabular_format.value,
            _META_SHEET_NAMES: meta.sheet_names,
            _META_SELECTED_SHEET: meta.selected_sheet,
        }
        meta_path = self._meta_dir / f"{meta.file_id}.json"
        meta_path.write_text(json.dumps(meta_payload, indent=2))

    def select_sheet(self, file_id: str, sheet_name: str | None) -> UploadedFileMeta:
        meta = self.load(file_id)
        if meta is None:
            raise FileNotFoundError(file_id)
        selected_sheet = _resolve_selected_sheet(meta, sheet_name)
        if selected_sheet is None:
            return meta
        updated = _with_selected_sheet(meta, selected_sheet)
        self._write_metadata(updated)
        return updated

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


def _sheet_names_for(path: Path) -> list[str]:
    """Return workbook sheet names, or [] for non-workbooks.

    Raises ``UnsupportedUploadError`` when a workbook-like file exists but its
    sheets cannot be read.
    """
    try:
        return [sheet.name for sheet in list_workbook_sheets(path)]
    except ValueError:
        return []
    except Exception as exc:
        raise UnsupportedUploadError(f"Unable to read workbook sheets: {exc}") from exc


def _tabular_format_from_metadata(payload: StoredMeta) -> TabularFormat:
    tabular_format = payload.get(_META_TABULAR_FORMAT)
    if tabular_format is not None:
        return TabularFormat(tabular_format)
    return get_tabular_format(Path(payload[_META_SAVED_NAME]), payload[_META_CONTENT_TYPE])


def _resolve_selected_sheet(meta: UploadedFileMeta, requested_sheet: str | None) -> str | None:
    """Return the worksheet to persist, or None when the upload has no sheets."""
    if not meta.sheet_names:
        return None
    selected_sheet = requested_sheet or meta.sheet_names[0]
    if selected_sheet not in meta.sheet_names:
        available = ", ".join(meta.sheet_names)
        raise ValueError(f"Unknown worksheet: {selected_sheet}. Available worksheets: {available}")
    return selected_sheet


def _with_selected_sheet(meta: UploadedFileMeta, selected_sheet: str) -> UploadedFileMeta:
    return UploadedFileMeta(
        file_id=meta.file_id,
        original_name=meta.original_name,
        content_type=meta.content_type,
        size_bytes=meta.size_bytes,
        saved_path=meta.saved_path,
        uploaded_at=meta.uploaded_at,
        tabular_format=meta.tabular_format,
        sheet_names=meta.sheet_names,
        selected_sheet=selected_sheet,
    )


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
