"""
Route column-to-CDE discovery through the Netrias recommendation API.

Axis of change: CDE recommendation service integration and response normalization.
"""

from __future__ import annotations

import logging
from pathlib import Path

from netrias_client import NetriasClient

from src.domain.manifest import ColumnMappingManifest

logger = logging.getLogger(__name__)
_UNAVAILABLE_MESSAGE = "Mapping discovery is unavailable."


class MappingDiscoveryUnavailableError(RuntimeError):
    """The mapping provider could not complete a request."""


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
    ) -> ColumnMappingManifest:
        if not self._client:
            raise MappingDiscoveryUnavailableError(_UNAVAILABLE_MESSAGE)

        try:
            raw_manifest = self._client.discover_mapping_from_tabular(
                source_path=csv_path,
                target_schema=data_model_key,
                external_version_number=external_version_number,
                confidence_threshold=0.7,
                sheet_name=sheet_name,
            )
        except Exception as exc:
            logger.warning(
                "Mapping discovery provider call failed",
                extra={"error_type": type(exc).__name__},
            )
            raise MappingDiscoveryUnavailableError(_UNAVAILABLE_MESSAGE) from exc

        return ColumnMappingManifest.from_payload_strict(raw_manifest)


__all__ = ["MappingDiscoveryService", "MappingDiscoveryUnavailableError"]
