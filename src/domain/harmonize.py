"""
Trigger harmonization jobs via the Netrias client SDK.

Abstracts SDK initialization and provides graceful fallback when API key is missing.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from netrias_client import NetriasClient

from src.domain.cde import CDEField, ColumnMapping, ColumnMappingSet, get_cde
from src.domain.manifest import ManifestPayload

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HarmonizeResult:
    job_id: str
    status: str
    detail: str
    job_id_available: bool = False
    manifest_path: Path | None = None


class HarmonizeService:
    def __init__(self) -> None:
        self._api_key: str | None = os.getenv("NETRIAS_API_KEY")
        self._client: NetriasClient | None = self._build_client()

    def _build_client(self) -> NetriasClient | None:
        if not self._api_key:
            logger.warning("NETRIAS_API_KEY missing; harmonize calls will be stubbed.")
            return None
        try:
            client = NetriasClient()
            client.configure(api_key=self._api_key)
            return client
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Failed to initialize NetriasClient", exc_info=exc)
            return None

    def run(
        self,
        *,
        file_path: Path,
        target_schema: str,
        column_mappings: ColumnMappingSet,
        manifest: ManifestPayload | None = None,
    ) -> HarmonizeResult:
        job_id = uuid4().hex
        if not self._client:
            detail = "Netrias client unavailable; returning a stubbed job."
            logger.warning(detail, extra={"job_id": job_id})
            return HarmonizeResult(job_id=job_id, status="queued", detail=detail)

        try:
            cde_map = self._prepare_cde_map(file_path, target_schema, manifest)
            _apply_column_mappings(cde_map, column_mappings)
            return self._execute_harmonization(file_path, cde_map, job_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Harmonize call failed; falling back to stub", exc_info=exc)
            return HarmonizeResult(job_id=job_id, status="failed", detail=str(exc))

    def _prepare_cde_map(
        self,
        file_path: Path,
        target_schema: str,
        manifest: ManifestPayload | None,
    ) -> ManifestPayload:
        if manifest is not None:
            return _normalize_manifest(manifest)
        return self._discover_cde_map(file_path=file_path, target_schema=target_schema)

    def _discover_cde_map(self, *, file_path: Path, target_schema: str) -> ManifestPayload:
        if not self._client:
            raise RuntimeError("Netrias client unavailable")
        raw_cde_map = self._client.discover_cde_mapping(
            source_csv=file_path,
            target_schema=target_schema,
            target_version="latest",
        )
        cde_map = _normalize_manifest(raw_cde_map)
        logger.info(
            "Discovered CDE map for harmonization",
            extra={"column_count": len(cde_map.get("column_mappings", {})), "target_schema": target_schema},
        )
        return cde_map

    def _execute_harmonization(
        self,
        file_path: Path,
        cde_map: ManifestPayload,
        fallback_job_id: str,
    ) -> HarmonizeResult:
        if not self._client:
            raise RuntimeError("Netrias client unavailable")

        # Note: netrias-client 0.1.0 renamed the parameter to 'manifest'; we pass our cde_map
        netrias_result = self._client.harmonize(source_path=file_path, manifest=cde_map)
        detail = str(getattr(netrias_result, "description", "Harmonization completed."))
        status = str(getattr(netrias_result, "status", "succeeded"))
        raw_job_id = getattr(netrias_result, "job_id", None)
        has_remote_job_id = bool(raw_job_id)
        remote_job_id = (
            str(raw_job_id)
            if has_remote_job_id
            else str(getattr(netrias_result, "mapping_id", "")) or fallback_job_id
        )
        manifest_path = _extract_manifest_path(netrias_result)
        logger.info(
            "Harmonization finished",
            extra={"file_path": str(file_path), "job_id": remote_job_id, "status": status},
        )
        return HarmonizeResult(
            job_id=remote_job_id,
            status=status,
            detail=detail,
            job_id_available=has_remote_job_id,
            manifest_path=manifest_path,
        )


def _apply_column_mappings(manifest: ManifestPayload, mappings: ColumnMappingSet) -> None:
    if not mappings.mappings:
        return

    column_map: MutableMapping[str, dict[str, object]] = manifest.setdefault("column_mappings", {})
    applied = mappings.get_applied()
    skipped = mappings.get_skipped()

    for mapping in applied:
        assert mapping.target is not None  # guaranteed by get_applied()
        column_map[mapping.column_name] = _build_mapping_entry(
            column_map.get(mapping.column_name),
            mapping.target,
        )

    for column_name in skipped:
        if column_name in column_map:
            del column_map[column_name]

    _log_mapping_results(applied, skipped)


def _build_mapping_entry(existing: Mapping[str, object] | None, cde_field: CDEField) -> dict[str, object]:
    entry = dict(existing or {})
    cde_def = get_cde(cde_field)
    entry["route"] = cde_def.route
    entry["targetField"] = cde_field.value
    entry["cde_id"] = cde_def.cde_id
    return entry


def _log_mapping_results(applied: list[ColumnMapping], skipped: list[str]) -> None:
    if applied:
        logger.info(
            "Applied column mappings",
            extra={"mappings": {m.column_name: m.target.value for m in applied if m.target}},
        )
    if skipped:
        logger.info("Skipped column mappings via 'No AI Recommendation'", extra={"columns": skipped})


def _normalize_manifest(manifest: Mapping[str, object] | object) -> ManifestPayload:
    if not isinstance(manifest, Mapping):
        return {"column_mappings": {}}

    column_mappings = manifest.get("column_mappings")
    if not isinstance(column_mappings, Mapping):
        return {"column_mappings": {}}

    return {"column_mappings": _filter_valid_columns(column_mappings)}


def _filter_valid_columns(entries: Mapping[object, object]) -> dict[str, dict[str, object]]:
    normalized: dict[str, dict[str, object]] = {}
    for column, entry in entries.items():
        if isinstance(column, str) and isinstance(entry, Mapping):
            normalized[column] = dict(entry)
    return normalized


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
