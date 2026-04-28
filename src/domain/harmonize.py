"""
Trigger harmonization jobs via the Netrias client SDK.

Abstracts SDK initialization and provides graceful fallback when API key is missing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from uuid import uuid4

from netrias_client import NetriasClient

from src.domain.cde import ColumnMapping, ColumnMappingSet
from src.domain.data_model_cache import SessionCache
from src.domain.manifest import ColumnMappingManifest, ColumnMappingRecord, ManifestPayload, normalize_manifest

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
        column_mappings: ColumnMappingSet,
        cache: SessionCache,
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
            cde_map = self._prepare_cde_map(file_path, target_schema, manifest, sheet_name)
            cde_map = _apply_column_mappings(cde_map, column_mappings, cache)
            return self._execute_harmonization(file_path, cde_map, job_id, target_schema, output_path, sheet_name)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Harmonize call failed; falling back to stub", exc_info=exc)
            return HarmonizeResult(job_id=job_id, status=HarmonizeStatus.FAILED, detail=str(exc))

    def _prepare_cde_map(
        self,
        file_path: Path,
        target_schema: str,
        manifest: ManifestPayload | None,
        sheet_name: str | None,
    ) -> ColumnMappingManifest:
        if manifest is not None:
            return ColumnMappingManifest.from_payload(manifest)
        return self._discover_cde_map(file_path=file_path, target_schema=target_schema, sheet_name=sheet_name)

    def _discover_cde_map(
        self,
        *,
        file_path: Path,
        target_schema: str,
        sheet_name: str | None,
    ) -> ColumnMappingManifest:
        if not self._client:
            raise RuntimeError("Netrias client unavailable")
        raw_cde_map = self._client.discover_mapping_from_tabular(
            source_path=file_path,
            target_schema=target_schema,
            target_version="latest",
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
        output_path: Path | None,
        sheet_name: str | None,
    ) -> HarmonizeResult:
        if not self._client:
            raise RuntimeError("Netrias client unavailable")

        netrias_result = self._client.harmonize(
            source_path=file_path,
            manifest=cde_map.to_payload(),
            data_commons_key=target_schema,
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
        )


def _apply_column_mappings(
    manifest: ColumnMappingManifest,
    mappings: ColumnMappingSet,
    cache: SessionCache,
) -> ColumnMappingManifest:
    if not mappings.mappings:
        return manifest

    applied = mappings.get_applied()
    skipped = mappings.get_skipped()
    updated = manifest

    for mapping in applied:
        updated = updated.with_record(_build_mapping_record(updated.records.get(mapping.column_key), mapping, cache))

    for column_key in skipped:
        updated = updated.without_column(column_key)

    _log_mapping_results(applied, [str(column_key) for column_key in skipped])
    return updated


def _build_mapping_record(
    existing: ColumnMappingRecord | None,
    mapping: ColumnMapping,
    cache: SessionCache,
) -> ColumnMappingRecord:
    """Look up cde_id from session cache (populated in Stage 2)."""
    if mapping.cde_key is None:
        raise ValueError("Cannot build a mapping record without a CDE key")
    cde_info = cache.get_cde_by_key(mapping.cde_key)
    if cde_info is None:
        raise ValueError(f"Unknown CDE key: {mapping.cde_key}")
    return ColumnMappingRecord(
        column_key=mapping.column_key,
        cde_key=mapping.cde_key,
        cde_id=cde_info.cde_id,
        column_name=existing.column_name if existing else None,
        harmonization=existing.harmonization if existing else None,
        route=existing.route if existing else None,
    )


def _log_mapping_results(applied: list[ColumnMapping], skipped: list[str]) -> None:
    if applied:
        logger.info(
            "Applied column mappings",
            extra={"mappings": {str(m.column_key): m.cde_key for m in applied if m.cde_key}},
        )
    if skipped:
        logger.info("Skipped column mappings via 'No Mapping'", extra={"columns": skipped})


def _extract_manifest_path(netrias_result: object) -> Path | None:
    raw_path = getattr(netrias_result, "manifest_path", None)
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
