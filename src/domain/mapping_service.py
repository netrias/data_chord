"""
Route column-to-CDE discovery through the Netrias recommendation API.

Axis of change: CDE recommendation service integration and response normalization.
"""

from __future__ import annotations

import logging
from pathlib import Path

from netrias_client import NetriasClient

from src.domain.cde import ModelSuggestion
from src.domain.manifest import ColumnMappingManifest, ManifestPayload

logger = logging.getLogger(__name__)


class MappingDiscoveryService:

    def __init__(self, client: NetriasClient | None) -> None:
        self._client = client
        if not client:
            logger.warning("NetriasClient unavailable; discovery calls will fail.")

    def discover(
        self,
        *,
        csv_path: Path,
        target_schema: str,
        sheet_name: str | None = None,
    ) -> tuple[dict[str, list[ModelSuggestion]], dict[str, str], ManifestPayload]:
        """manual_overrides (pos 2) always empty — preserved for caller interface compatibility."""
        if not self._client:
            raise RuntimeError("NetriasClient unavailable (missing NETRIAS_API_KEY)")

        try:
            raw_manifest = self._client.discover_mapping_from_tabular(
                source_path=csv_path,
                target_schema=target_schema,
                confidence_threshold=0.7,
                sheet_name=sheet_name,
            )
        except Exception as exc:
            raise RuntimeError(f"CDE discovery failed: {exc}") from exc

        manifest = ColumnMappingManifest.from_payload(raw_manifest)
        return manifest.suggestions_by_column(), {}, manifest.to_payload()
