"""
Route column-to-CDE discovery through the Netrias recommendation API.

Axis of change: CDE recommendation service integration and response normalization.
"""

from __future__ import annotations

import logging
from pathlib import Path

from netrias_client import NetriasClient

from src.domain.cde import ModelSuggestion
from src.domain.harmonize import normalize_manifest
from src.domain.manifest import ManifestPayload

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
    ) -> tuple[dict[str, list[ModelSuggestion]], dict[str, str], ManifestPayload]:
        """manual_overrides (pos 2) always empty — preserved for caller interface compatibility."""
        if not self._client:
            raise RuntimeError("NetriasClient unavailable (missing NETRIAS_API_KEY)")

        try:
            raw_manifest = self._client.discover_mapping_from_csv(
                source_csv=csv_path,
                target_schema=target_schema,
                confidence_threshold=0.7,
            )
        except Exception as exc:
            raise RuntimeError(f"CDE discovery failed: {exc}") from exc

        manifest = normalize_manifest(raw_manifest)
        cde_targets = _cde_targets_from_manifest(manifest)
        return cde_targets, {}, manifest


def _cde_targets_from_manifest(
    manifest: ManifestPayload,
) -> dict[str, list[ModelSuggestion]]:
    """AnalyzeResponse needs per-column suggestions for frontend display."""
    column_mappings = manifest.get("column_mappings", {})
    targets: dict[str, list[ModelSuggestion]] = {}
    for column_name, entry in column_mappings.items():
        target_field = entry["targetField"]
        if target_field:
            targets[column_name] = [ModelSuggestion(target=target_field, similarity=1.0)]
    return targets
