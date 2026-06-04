"""
Trigger harmonization jobs via the Netrias client SDK.

Abstracts SDK initialization and provides graceful fallback when API key is missing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

from netrias_client import NetriasClient

from src.domain.column_cde_map import ColumnCdeOverrides
from src.domain.column_renames import ColumnRenameSet
from src.domain.columns import ColumnKey
from src.domain.data_model_cache import SessionCache
from src.domain.manifest import (
    DEFAULT_HARMONIZATION,
    ColumnMappingManifest,
    ColumnMappingRecord,
    ManifestPayload,
    normalize_manifest,
)

logger = logging.getLogger(__name__)


class HarmonizeStatus(str, Enum):
    QUEUED = "queued"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class HarmonizeResult:
    job_id: str
    status: HarmonizeStatus
    detail: str
    job_id_available: bool = False
    manifest_path: Path | None = None
    output_path: Path | None = None


class _ExternalVersionHarmonizeClient(Protocol):
    def discover_mapping_from_tabular(
        self,
        *,
        source_path: Path,
        target_schema: str,
        target_version: str,
        sheet_name: str | None,
    ) -> ManifestPayload: ...

    def harmonize(
        self,
        *,
        source_path: Path,
        manifest: ManifestPayload,
        data_commons_key: str,
        external_version_number: str,
        output_path: Path | None,
        sheet_name: str | None,
    ) -> object: ...


class HarmonizeService:
    def __init__(self, client: NetriasClient | None) -> None:
        self._client = client
        if not client:
            logger.warning("NetriasClient unavailable; harmonize calls will be stubbed.")

    def run(
        self,
        *,
        file_path: Path,
        target_schema: str,
        column_overrides: ColumnCdeOverrides,
        column_renames: ColumnRenameSet,
        cache: SessionCache,
        external_version_number: str,
        manifest: ManifestPayload | None = None,
        output_path: Path | None = None,
        sheet_name: str | None = None,
    ) -> HarmonizeResult:
        job_id = uuid4().hex
        if not self._client:
            detail = "Netrias client unavailable; returning a stubbed job."
            logger.warning(detail, extra={"job_id": job_id})
            return HarmonizeResult(job_id=job_id, status=HarmonizeStatus.QUEUED, detail=detail)

        try:
            cde_map = self._prepare_cde_map(
                file_path,
                target_schema,
                external_version_number,
                manifest,
                sheet_name,
            )
            cde_map = _apply_column_updates(cde_map, column_overrides, column_renames, cache)
            return self._execute_harmonization(
                file_path,
                cde_map,
                job_id,
                target_schema,
                external_version_number,
                output_path,
                sheet_name,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Harmonize call failed; falling back to stub", exc_info=exc)
            return HarmonizeResult(job_id=job_id, status=HarmonizeStatus.FAILED, detail=str(exc))

    def _prepare_cde_map(
        self,
        file_path: Path,
        target_schema: str,
        external_version_number: str,
        manifest: ManifestPayload | None,
        sheet_name: str | None,
    ) -> ColumnMappingManifest:
        if manifest is not None:
            return ColumnMappingManifest.from_payload(manifest)
        return self._discover_cde_map(
            file_path=file_path,
            target_schema=target_schema,
            external_version_number=external_version_number,
            sheet_name=sheet_name,
        )

    def _discover_cde_map(
        self,
        *,
        file_path: Path,
        target_schema: str,
        external_version_number: str,
        sheet_name: str | None,
    ) -> ColumnMappingManifest:
        if not self._client:
            raise RuntimeError("Netrias client unavailable")
        external_version_client = cast(_ExternalVersionHarmonizeClient, self._client)
        raw_cde_map = external_version_client.discover_mapping_from_tabular(
            source_path=file_path,
            target_schema=target_schema,
            target_version=external_version_number,
            sheet_name=sheet_name,
        )
        cde_map = ColumnMappingManifest.from_payload(raw_cde_map)
        logger.info(
            "Discovered CDE map for harmonization",
            extra={"column_count": len(cde_map.records), "target_schema": target_schema},
        )
        return cde_map

    def _execute_harmonization(
        self,
        file_path: Path,
        cde_map: ColumnMappingManifest,
        fallback_job_id: str,
        target_schema: str,
        external_version_number: str,
        output_path: Path | None,
        sheet_name: str | None,
    ) -> HarmonizeResult:
        if not self._client:
            raise RuntimeError("Netrias client unavailable")

        external_version_client = cast(_ExternalVersionHarmonizeClient, self._client)
        netrias_result = external_version_client.harmonize(
            source_path=file_path,
            manifest=cde_map.to_payload(),
            data_commons_key=target_schema,
            external_version_number=external_version_number,
            output_path=output_path,
            sheet_name=sheet_name,
        )
        detail = str(getattr(netrias_result, "description", "Harmonization completed."))
        raw_status = str(getattr(netrias_result, "status", "succeeded"))
        try:
            status = HarmonizeStatus(raw_status)
        except ValueError:
            status = HarmonizeStatus.SUCCEEDED
        raw_job_id = getattr(netrias_result, "job_id", None)
        raw_mapping_id = getattr(netrias_result, "mapping_id", None)
        has_remote_job_id = bool(raw_job_id)
        remote_job_id = (
            str(raw_job_id)
            if has_remote_job_id
            else str(raw_mapping_id) if raw_mapping_id else fallback_job_id
        )
        manifest_path = _extract_manifest_path(netrias_result)
        output_path = _extract_output_path(netrias_result)
        logger.info(
            "Harmonization finished",
            extra={"file_path": str(file_path), "job_id": remote_job_id, "status": status.value},
        )
        return HarmonizeResult(
            job_id=remote_job_id,
            status=status,
            detail=detail,
            job_id_available=has_remote_job_id,
            manifest_path=manifest_path,
            output_path=output_path,
        )


def _apply_column_updates(
    manifest: ColumnMappingManifest,
    overrides: ColumnCdeOverrides,
    renames: ColumnRenameSet,
    cache: SessionCache,
) -> ColumnMappingManifest:
    if overrides.is_empty and not renames.renames:
        return manifest

    applied = overrides.applied_items()
    skipped = overrides.skipped_columns()
    updated = manifest

    for column_key, cde_key in applied:
        updated = updated.with_record(
            _build_mapping_record(updated.records.get(column_key), column_key, cde_key, cache)
        )

    for column_key in skipped:
        updated = updated.without_column(column_key)

    updated = updated.with_column_names(renames.renames)
    _log_mapping_results(applied, [str(column_key) for column_key in skipped], renames)
    return updated


def _build_mapping_record(
    existing: ColumnMappingRecord | None,
    column_key: ColumnKey,
    cde_key: str,
    cache: SessionCache,
) -> ColumnMappingRecord:
    """Look up cde_id from session cache (populated in Stage 2)."""
    cde_info = cache.get_cde_by_key(cde_key)
    if cde_info is None:
        raise ValueError(f"Unknown CDE key: {cde_key}")
    return ColumnMappingRecord(
        column_key=column_key,
        cde_key=cde_key,
        cde_id=cde_info.cde_id,
        column_name=existing.column_name if existing else str(column_key),
        harmonization=existing.harmonization if existing else DEFAULT_HARMONIZATION,
        route=existing.route if existing else None,
        alternatives=existing.alternatives if existing else (),
    )


def _log_mapping_results(
    applied: list[tuple[ColumnKey, str]],
    skipped: list[str],
    renames: ColumnRenameSet,
) -> None:
    if applied:
        logger.info(
            "Applied column mappings",
            extra={"mappings": {str(column_key): cde_key for column_key, cde_key in applied}},
        )
    if skipped:
        logger.info("Skipped column mappings via 'No Mapping'", extra={"columns": skipped})
    if renames.renames:
        logger.info("Applied column renames", extra={"renames": renames.to_strings()})


def _extract_manifest_path(netrias_result: object) -> Path | None:
    raw_path = getattr(netrias_result, "manifest_path", None)
    return _existing_path_from_value(raw_path)


def _extract_output_path(netrias_result: object) -> Path | None:
    raw_path = getattr(netrias_result, "file_path", None)
    return _existing_path_from_value(raw_path)


def _existing_path_from_value(raw_path: object) -> Path | None:
    if raw_path is None:
        return None
    if isinstance(raw_path, Path):
        return raw_path if raw_path.exists() else None
    if isinstance(raw_path, str) and raw_path:
        path = Path(raw_path)
        return path if path.exists() else None
    return None


__all__ = [
    "HarmonizeResult",
    "HarmonizeService",
    "HarmonizeStatus",
    "normalize_manifest",
]
