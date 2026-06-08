"""
Route column-to-CDE discovery through the Netrias recommendation API.

Axis of change: CDE recommendation service integration and response normalization.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from netrias_client import NetriasClient

from src.domain.cde import ModelSuggestion
from src.domain.manifest import ColumnMappingManifest, ManifestPayload

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MappingDiscoveryResult:
    """Domain result of mapping discovery, with payload views derived at the edge."""

    manifest: ColumnMappingManifest

    @property
    def cde_targets(self) -> dict[str, list[ModelSuggestion]]:
        return self.manifest.suggestions_by_column()

    @property
    def manifest_payload(self) -> ManifestPayload:
        return self.manifest.to_payload()


class _ExternalVersionMappingClient(Protocol):
    def discover_mapping_from_tabular(
        self,
        *,
        source_path: Path,
        target_schema: str,
        target_version: str,
        confidence_threshold: float,
        sheet_name: str | None,
    ) -> ManifestPayload: ...


class MappingDiscoveryService:

    def __init__(self, client: NetriasClient | None) -> None:
        self._client = client
        if not client:
            logger.warning("NetriasClient unavailable; discovery calls will fail.")

    def discover(
        self,
        *,
        csv_path: Path,
        data_model_key: str,
        external_version_number: str,
        sheet_name: str | None = None,
    ) -> MappingDiscoveryResult:
        if not self._client:
            raise RuntimeError("NetriasClient unavailable (missing NETRIAS_API_KEY)")

        try:
            external_version_client = cast(_ExternalVersionMappingClient, self._client)
            raw_manifest = external_version_client.discover_mapping_from_tabular(
                source_path=csv_path,
                target_schema=data_model_key,
                target_version=external_version_number,
                confidence_threshold=0.7,
                sheet_name=sheet_name,
            )
        except Exception as exc:
            raise RuntimeError(f"CDE discovery failed: {exc}") from exc

        manifest = ColumnMappingManifest.from_payload(raw_manifest)
        return MappingDiscoveryResult(manifest=manifest)


__all__ = ["MappingDiscoveryResult", "MappingDiscoveryService"]
