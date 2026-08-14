"""
Trigger harmonization jobs via the Netrias client SDK.

Accepts a prepared mapping manifest and isolates SDK response handling.
"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import uuid4

from netrias_client import NetriasClient

from src.domain.harmonization import HarmonizeStatus
from src.domain.manifest import ColumnMappingManifest
from src.integrations.harmonize import HarmonizeResult
from src.persistence.manifest_reader import read_manifest_parquet
from src.persistence.manifest_writer import write_manifest_parquet
from src.persistence.pv_manifest_store import ColumnPvSets

logger = logging.getLogger(__name__)


class HarmonizeService:
    def __init__(self, client: NetriasClient | None) -> None:
        if not client:
            raise RuntimeError("NetriasClient unavailable")
        self._client = client

    def run(
        self,
        *,
        file_path: Path,
        data_model_key: str,
        external_version_number: str,
        prepared_manifest: ColumnMappingManifest,
        column_pv_sets: ColumnPvSets,
        output_path: Path | None = None,
        sheet_name: str | None = None,
    ) -> HarmonizeResult:
        job_id = uuid4().hex
        try:
            return self._execute_harmonization(
                file_path,
                prepared_manifest,
                job_id,
                data_model_key,
                external_version_number,
                output_path,
                sheet_name,
            )
        except Exception as exc:  # pragma: no cover - defensive SDK boundary
            logger.exception("Harmonize provider call failed", exc_info=exc, extra={"job_id": job_id})
            return HarmonizeResult(
                job_id=job_id,
                status=HarmonizeStatus.FAILED,
                detail="Harmonization provider failed.",
            )

    def _execute_harmonization(
        self,
        file_path: Path,
        cde_map: ColumnMappingManifest,
        fallback_job_id: str,
        data_model_key: str,
        external_version_number: str,
        output_path: Path | None,
        sheet_name: str | None,
    ) -> HarmonizeResult:
        netrias_result = self._client.harmonize(
            source_path=file_path,
            manifest=cde_map.to_payload(),
            target_schema=data_model_key,
            external_version_number=external_version_number,
            output_path=output_path,
            sheet_name=sheet_name,
        )
        raw_status = getattr(netrias_result, "status", None)
        if not isinstance(raw_status, str):
            raise ValueError("Harmonization provider response has no status")
        try:
            status = HarmonizeStatus(raw_status)
        except ValueError as exc:
            raise ValueError("Harmonization provider returned an unknown status") from exc
        detail = (
            str(getattr(netrias_result, "description", "Harmonization completed."))
            if status != HarmonizeStatus.FAILED
            else "Harmonization provider failed."
        )
        raw_job_id = getattr(netrias_result, "job_id", None)
        raw_mapping_id = getattr(netrias_result, "mapping_id", None)
        has_remote_job_id = bool(raw_job_id)
        remote_job_id = (
            str(raw_job_id)
            if has_remote_job_id
            else str(raw_mapping_id) if raw_mapping_id else fallback_job_id
        )
        manifest_path = _extract_manifest_path(netrias_result)
        if manifest_path is not None:
            _normalize_manifest(manifest_path)
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


def _normalize_manifest(manifest_path: Path) -> None:
    """Drop the legacy provider confidence field at the adapter boundary."""
    manifest = read_manifest_parquet(manifest_path)
    if manifest is None or not write_manifest_parquet(manifest_path, manifest.rows):
        raise ValueError("Harmonization provider returned an unreadable manifest")


__all__ = [
    "HarmonizeService",
]
