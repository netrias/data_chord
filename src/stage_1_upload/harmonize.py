"""Trigger harmonization jobs via the Netrias client SDK."""

from __future__ import annotations

import importlib
import logging
import os
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

logger = logging.getLogger(__name__)

ManifestPayload = dict[str, dict[str, dict[str, object]]]
TARGET_ALIAS_MAP: Mapping[str, str] = {
    "primary diagnosis": "primary_diagnosis",
    "primary_diagnosis": "primary_diagnosis",
    "morphology": "morphology",
    "sample anatomic site": "sample_anatomic_site",
    "sample_anatomic_site": "sample_anatomic_site",
    "therapeutic agents": "therapeutic_agents",
    "therapeutic_agents": "therapeutic_agents",
    "tissue or organ of origin": "tissue_or_organ_of_origin",
    "tissue_or_organ_of_origin": "tissue_or_organ_of_origin",
}
TARGET_METADATA: Mapping[str, dict[str, object]] = {
    "primary_diagnosis": {
        "route": "sagemaker:primary",
        "targetField": "primary_diagnosis",
        "cdeId": 2,
        "cde_id": 2,
    },
    "morphology": {
        "route": "sagemaker:morphology",
        "targetField": "morphology",
        "cdeId": 3,
        "cde_id": 3,
    },
    "sample_anatomic_site": {
        "route": "sagemaker:sample_anatomic_site",
        "targetField": "sample_anatomic_site",
        "cdeId": 5,
        "cde_id": 5,
    },
    "therapeutic_agents": {
        "route": "sagemaker:therapeutic_agents",
        "targetField": "therapeutic_agents",
        "cdeId": 1,
        "cde_id": 1,
    },
    "tissue_or_organ_of_origin": {
        "route": "sagemaker:tissue_or_organ_of_origin",
        "targetField": "tissue_or_organ_of_origin",
        "cdeId": 4,
        "cde_id": 4,
    },
}


@dataclass(frozen=True)
class HarmonizeResult:
    """why: capture essential metadata about a harmonization run."""

    job_id: str
    status: str
    detail: str
    job_id_available: bool = False


class HarmonizeClientProtocol(Protocol):
    """why: describe the subset of the SDK we rely on."""

    def configure(self, *, api_key: str) -> None:
        ...

    def discover_cde_mapping(self, *, source_csv: Path, target_schema: str, sample_limit: int = 25) -> Mapping[str, object]:
        ...

    def harmonize(
        self,
        *,
        source_path: Path,
        manifest: Path | Mapping[str, object],
    ) -> object:
        ...


class NetriasClientConstructor(Protocol):
    """why: describe how to instantiate the Netrias client."""

    def __call__(self) -> HarmonizeClientProtocol:
        ...


class HarmonizeService:
    """why: abstract the Netrias client and allow graceful fallbacks."""

    def __init__(self) -> None:
        self._api_key: str | None = os.getenv("NETRIAS_API_KEY")
        self._client: HarmonizeClientProtocol | None = self._build_client()

    def _build_client(self) -> HarmonizeClientProtocol | None:
        try:
            module = importlib.import_module("netrias_client")
            client_cls = cast(NetriasClientConstructor, getattr(module, "NetriasClient"))
        except (ModuleNotFoundError, AttributeError):
            return None
        if not self._api_key:
            logger.warning("NETRIAS_API_KEY missing; harmonize calls will be stubbed.")
            return None
        client = client_cls()  # type: ignore[call-arg]
        client.configure(api_key=self._api_key)
        return client

    def run(
        self,
        *,
        file_path: Path,
        target_schema: str,
        manual_overrides: dict[str, str],
        manifest: ManifestPayload | None = None,
    ) -> HarmonizeResult:
        job_id = uuid4().hex
        if not self._client:
            detail = "Netrias client unavailable; returning a stubbed job."
            logger.warning(detail, extra={"job_id": job_id})
            return HarmonizeResult(job_id=job_id, status="queued", detail=detail)

        try:
            if manifest is None:
                manifest_payload = self._discover_manifest(file_path=file_path, target_schema=target_schema)
            else:
                manifest_payload = _normalize_manifest(manifest)
            if manual_overrides:
                self._apply_manual_overrides(manifest_payload, manual_overrides)
            netrias_result = self._client.harmonize(source_path=file_path, manifest=manifest_payload)
            detail = str(getattr(netrias_result, "description", "Harmonization completed."))
            status = str(getattr(netrias_result, "status", "succeeded"))
            raw_job_id = getattr(netrias_result, "job_id", None)
            has_remote_job_id = bool(raw_job_id)
            remote_job_id = (
                str(raw_job_id)
                if has_remote_job_id
                else str(getattr(netrias_result, "mapping_id", "")) or job_id
            )
            logger.info(
                "Harmonization finished",
                extra={"file_path": str(file_path), "job_id": remote_job_id, "status": status},
            )
            return HarmonizeResult(
                job_id=remote_job_id,
                status=status,
                detail=detail,
                job_id_available=has_remote_job_id,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Harmonize call failed; falling back to stub", exc_info=exc)
            return HarmonizeResult(job_id=job_id, status="failed", detail=str(exc))

    def _discover_manifest(self, *, file_path: Path, target_schema: str) -> ManifestPayload:
        if not self._client:
            raise RuntimeError("Netrias client unavailable")
        raw_manifest = self._client.discover_cde_mapping(source_csv=file_path, target_schema=target_schema)
        manifest = _normalize_manifest(raw_manifest)
        logger.info(
            "Discovered manifest for harmonization",
            extra={"column_count": len(manifest.get("column_mappings", {})), "target_schema": target_schema},
        )
        return manifest

    def _apply_manual_overrides(self, manifest: ManifestPayload, overrides: Mapping[str, str]) -> None:
        column_mappings: MutableMapping[str, dict[str, object]] = manifest.setdefault("column_mappings", {})
        applied: dict[str, str] = {}
        for column, selection in overrides.items():
            normalized_target = _normalize_target_name(selection)
            if not normalized_target:
                continue
            entry = _override_entry(column_mappings.get(column), normalized_target)
            column_mappings[column] = entry
            applied[column] = normalized_target
        if applied:
            logger.info("Applied manual overrides", extra={"overrides": applied})


def _normalize_manifest(manifest: Mapping[str, object] | object) -> ManifestPayload:
    if not isinstance(manifest, Mapping):
        return {"column_mappings": {}}
    column_mappings = manifest.get("column_mappings")
    if not isinstance(column_mappings, Mapping):
        return {"column_mappings": {}}
    normalized: dict[str, dict[str, object]] = {}
    for column, entry in column_mappings.items():
        if isinstance(column, str) and isinstance(entry, Mapping):
            normalized[column] = dict(entry)
    return {"column_mappings": normalized}


def _normalize_target_name(selection: str | None) -> str | None:
    if not selection:
        return None
    cleaned = selection.strip().lower().replace("-", " ")
    slug = "_".join(part for part in cleaned.split() if part)
    if not slug:
        return None
    return TARGET_ALIAS_MAP.get(slug, slug)


def _override_entry(existing: Mapping[str, object] | None, target: str) -> dict[str, object]:
    entry = dict(existing or {})
    metadata = TARGET_METADATA.get(target)
    if metadata:
        entry.update(metadata)
    else:
        entry["targetField"] = target
    return entry
