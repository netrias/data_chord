"""
Trigger harmonization jobs via the Netrias client SDK.

Abstracts SDK initialization and provides graceful fallback when API key is missing.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from uuid import uuid4

from netrias_client import AlternativeEntry, ColumnMappingRecord, ManifestPayload, MappingValidationError, NetriasClient

from src.domain.column_assignment import ColumnAssignment
from src.domain.data_model_cache import SessionCache

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
        assignments: dict[int, ColumnAssignment],
        cache: SessionCache,
        manifest: ManifestPayload | None = None,
    ) -> HarmonizeResult:
        job_id = uuid4().hex
        if not self._client:
            detail = "Netrias client unavailable; returning a stubbed job."
            logger.warning(detail, extra={"job_id": job_id})
            return HarmonizeResult(job_id=job_id, status=HarmonizeStatus.QUEUED, detail=detail)

        try:
            cde_map = self._prepare_cde_map(file_path, target_schema, manifest)
            _apply_column_mappings(cde_map, assignments, cache)
            return self._execute_harmonization(file_path, cde_map, job_id, target_schema)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Harmonize call failed; falling back to stub", exc_info=exc)
            return HarmonizeResult(job_id=job_id, status=HarmonizeStatus.FAILED, detail=str(exc))

    def _prepare_cde_map(
        self,
        file_path: Path,
        target_schema: str,
        manifest: ManifestPayload | None,
    ) -> ManifestPayload:
        if manifest is not None:
            return normalize_manifest(manifest)
        return self._discover_cde_map(file_path=file_path, target_schema=target_schema)

    def _discover_cde_map(self, *, file_path: Path, target_schema: str) -> ManifestPayload:
        if not self._client:
            raise RuntimeError("Netrias client unavailable")
        raw_cde_map = self._client.discover_mapping_from_csv(
            source_csv=file_path,
            target_schema=target_schema,
            target_version="latest",
        )
        cde_map = normalize_manifest(raw_cde_map)
        logger.info(
            "Discovered CDE map for harmonization",
            extra={"column_count": len(cde_map["column_mappings"]), "target_schema": target_schema},
        )
        return cde_map

    def _execute_harmonization(
        self,
        file_path: Path,
        cde_map: ManifestPayload,
        fallback_job_id: str,
        target_schema: str,
    ) -> HarmonizeResult:
        if not self._client:
            raise RuntimeError("Netrias client unavailable")

        netrias_result = self._client.harmonize(
            source_path=file_path, manifest=cde_map, data_commons_key=target_schema
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
    manifest: ManifestPayload,
    assignments: dict[int, ColumnAssignment],
    cache: SessionCache,
) -> None:
    """Write position-keyed column mappings from resolved assignments."""
    if not assignments:
        return

    max_id = max(assignments.keys())
    entries: list[ColumnMappingRecord | None] = [None] * (max_id + 1)
    applied: list[tuple[str, str]] = []
    skipped: list[str] = []

    for column_id in sorted(assignments):
        assignment = assignments[column_id]
        if assignment.cde_key is None:
            skipped.append(assignment.column_name)
            continue
        cde_key = assignment.cde_key
        existing = _find_existing_entry(manifest, column_id)
        cde_id = _resolve_cde_id(cde_key, existing, cache)
        if cde_id is None:
            raise ValueError(f"Unknown CDE key: {cde_key}")
        # Carry forward alternatives from SDK discovery; user selection doesn't add new ones.
        raw_alts = existing.get("alternatives") if existing is not None else None
        alternatives: list[AlternativeEntry] = list(raw_alts) if isinstance(raw_alts, list) else []
        entry: ColumnMappingRecord = {
            "column_name": assignment.column_name,
            "cde_key": cde_key,
            "cde_id": cde_id,
            "alternatives": alternatives,
        }
        entries[column_id] = entry
        applied.append((assignment.column_name, cde_key))

    manifest["column_mappings"] = entries
    _log_mapping_results(applied, skipped)


def _find_existing_entry(
    manifest: ManifestPayload,
    column_id: int,
) -> Mapping[str, object] | None:
    """Look up existing manifest entry so alternatives can be preserved."""
    column_mappings = manifest.get("column_mappings")
    if not isinstance(column_mappings, list):
        return None
    if 0 <= column_id < len(column_mappings):
        entry = column_mappings[column_id]
        return entry if isinstance(entry, Mapping) else None
    return None


def _resolve_cde_id(
    cde_key: str,
    existing: Mapping[str, object] | None,
    cache: SessionCache,
) -> int | None:
    """Prefer cache (authoritative after discovery); fall back to existing entry's cde_id
    when the cache hasn't been populated (e.g. Stage 3 invoked without a prior Stage 1 in
    this process) but the SDK-produced manifest already carries cde_id."""
    cde_info = cache.get_cde_by_key(cde_key)
    if cde_info is not None:
        return cde_info.cde_id
    if existing is not None and existing.get("cde_key") == cde_key:
        existing_id = existing.get("cde_id")
        if isinstance(existing_id, int):
            return existing_id
    return None


def _log_mapping_results(applied: list[tuple[str, str]], skipped: list[str]) -> None:
    if applied:
        logger.info(
            "Applied column mappings",
            extra={"mappings": dict(applied)},
        )
    if skipped:
        logger.info("Skipped column mappings via 'No Mapping'", extra={"columns": skipped})


def normalize_manifest(manifest: object) -> ManifestPayload:
    """Strict boundary validator — raises MappingValidationError on any deviation from the
    canonical wire shape so callers never see partial or legacy data.
    """
    if not isinstance(manifest, Mapping):
        raise MappingValidationError(
            f"expected Mapping, found {type(manifest).__name__} [source: normalize_manifest]"
        )
    if "column_mappings" not in manifest:
        raise MappingValidationError(
            f"expected key 'column_mappings', found keys: {list(manifest.keys())} "
            f"[source: normalize_manifest]"
        )
    column_mappings = manifest["column_mappings"]
    if not isinstance(column_mappings, list):
        raise MappingValidationError(
            f"expected list for 'column_mappings', found {type(column_mappings).__name__} "
            f"[source: normalize_manifest]"
        )
    validated: list[ColumnMappingRecord | None] = [
        _validate_entry(entry, idx) for idx, entry in enumerate(column_mappings)
    ]
    return {"column_mappings": validated}


def _validate_entry(entry: object, idx: int) -> ColumnMappingRecord | None:
    """Validate a single list slot; None is a valid 'no mapping' sentinel."""
    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise MappingValidationError(
            f"expected dict or None at column_mappings[{idx}], found {type(entry).__name__}"
        )
    for key, expected_type in (
        ("column_name", str),
        ("cde_key", str),
        ("cde_id", int),
        ("alternatives", list),
    ):
        if key not in entry:
            raise MappingValidationError(
                f"expected key '{key}' at column_mappings[{idx}], "
                f"found keys: {list(entry.keys())}"
            )
        if not isinstance(entry[key], expected_type):
            raise MappingValidationError(
                f"expected '{key}': {expected_type.__name__} at column_mappings[{idx}], "
                f"found '{key}': {type(entry[key]).__name__}"
            )
    for alt_idx, alt in enumerate(entry["alternatives"]):
        _validate_alternative(alt, idx, alt_idx)
    return entry  # type: ignore[return-value]


def _validate_alternative(alt: object, entry_idx: int, alt_idx: int) -> None:
    """Each alternative must carry target (str) and confidence (float)."""
    source = f"column_mappings[{entry_idx}].alternatives[{alt_idx}]"
    if not isinstance(alt, dict):
        raise MappingValidationError(
            f"expected dict at {source}, found {type(alt).__name__}"
        )
    if "target" not in alt or not isinstance(alt["target"], str):
        raise MappingValidationError(
            f"expected 'target': str at {source}, found keys: {list(alt.keys())}"
        )
    if "confidence" not in alt or not isinstance(alt["confidence"], (int, float)):
        raise MappingValidationError(
            f"expected 'confidence': float at {source}, found keys: {list(alt.keys())}"
        )


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
