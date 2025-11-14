"""Handle persistence and lightweight profiling for the upload stage."""

from __future__ import annotations

import csv
import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, cast
from uuid import uuid4

from fastapi import UploadFile

from .schemas import ColumnPreview, ConfidenceBucket

logger = logging.getLogger(__name__)
DEFAULT_CDE_SAMPLE_LIMIT = 50
CSVRow = dict[str, str | None]


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
        filename = upload.filename or "dataset.csv"
        suffix = Path(filename).suffix.lower() or ".csv"
        content_type = (upload.content_type or "text/csv").lower()
        self._validate_upload(suffix, content_type)

        file_id = uuid4().hex
        destination = self._data_dir / f"{file_id}{suffix}"
        total_bytes = 0

        try:
            with destination.open("wb") as target:
                while chunk := await upload.read(self._constraints.chunk_size):
                    total_bytes += len(chunk)
                    if total_bytes > self._constraints.max_bytes:
                        raise UploadTooLargeError(
                            f"File exceeds {self._constraints.max_bytes} bytes limit",
                        )
                    _ = target.write(chunk)
        except UploadTooLargeError:
            destination.unlink(missing_ok=True)
            logger.warning("Upload aborted because it was too large", extra={"file": filename})
            raise
        finally:
            await upload.close()

        meta = UploadedFileMeta(
            file_id=file_id,
            original_name=filename,
            content_type=content_type,
            size_bytes=total_bytes,
            saved_path=destination,
            uploaded_at=datetime.now(timezone.utc),
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
        saved_name: str = payload["saved_name"]
        return UploadedFileMeta(
            file_id=file_id,
            original_name=payload["original_name"],
            content_type=payload["content_type"],
            size_bytes=payload["size_bytes"],
            saved_path=self._data_dir / saved_name,
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
        _ = meta_path.write_text(json.dumps(meta_payload, indent=2))

    def save_manifest(self, file_id: str, manifest: Mapping[str, object]) -> Path:
        """why: persist the harmonization manifest for reuse across stages."""

        path = self._manifest_dir / f"{file_id}.json"
        _ = path.write_text(json.dumps(manifest, indent=2))
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


def analyze_columns(csv_path: Path, max_preview_rows: int = 5) -> tuple[int, list[ColumnPreview]]:
    """why: produce basic column summaries without heavy dependencies."""
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        total_rows = 0
        sample_rows: list[dict[str, str]] = []
        for row in reader:
            total_rows += 1
            if len(sample_rows) < max_preview_rows:
                sample_rows.append(row)

    columns: list[ColumnPreview] = []
    for header in headers:
        samples = [_normalize_sample(row.get(header, "")) for row in sample_rows]
        trimmed = [value for value in samples if value]
        non_empty = len(trimmed)
        sample_size = max(len(samples), 1)
        confidence = _confidence_bucket(non_empty, sample_size)
        score = non_empty / sample_size if sample_size else 0.0
        inferred_type = _infer_type(trimmed)
        columns.append(
            ColumnPreview(
                column_name=header,
                inferred_type=inferred_type,
                sample_values=samples,
                confidence_bucket=confidence,
                confidence_score=round(score, 2),
            )
        )

    return total_rows, columns


def _normalize_sample(value: str | None) -> str:
    """why: make sure preview values are printable."""
    if value is None:
        return ""
    sanitized = value.strip()
    return sanitized[:80]


def _confidence_bucket(non_empty: int, sample_size: int) -> ConfidenceBucket:
    """why: derive a friendly signal for how complete sample data is."""
    if sample_size == 0:
        return "low"
    ratio = non_empty / sample_size
    if ratio >= 0.8:
        return "high"
    if ratio >= 0.5:
        return "medium"
    return "low"


def _infer_type(values: Iterable[str]) -> str:
    """why: guess a helpful label for the column's dominant type."""
    cleaned = [value.replace(",", "") for value in values if value]
    if cleaned and _looks_numeric(cleaned):
        return "numeric"
    if cleaned and _looks_date(cleaned):
        return "date"
    if cleaned:
        return "text"
    return "unknown"


def _looks_numeric(values: list[str]) -> bool:
    """why: check if every value parses as a float."""
    try:
        for value in values:
            _ = float(value)
        return True
    except ValueError:
        return False


def _looks_date(values: list[str]) -> bool:
    """why: approximate detection for short, common date formats."""
    formats = ["%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"]
    for candidate in values:
        for fmt in formats:
            try:
                _ = datetime.strptime(candidate, fmt)
                break
            except ValueError:
                continue
        else:
            return False
    return bool(values)


def build_cde_payload(
    csv_path: Path,
    headers: list[str],
    limit: int = DEFAULT_CDE_SAMPLE_LIMIT,
) -> dict[str, list[str]]:
    """why: produce the JSON structure expected by the CDE API."""
    if not headers:
        return {}

    payload: dict[str, list[str]] = {header: [] for header in headers}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        total = 0
        for row_raw in reader:
            row: CSVRow = row_raw
            for header in headers:
                payload[header].append(_normalize_sample(row.get(header, "")))
            total += 1
            if total >= limit:
                break

    max_length = max((len(values) for values in payload.values()), default=0)
    for values in payload.values():
        if len(values) < max_length:
            values.extend([""] * (max_length - len(values)))

    return payload
