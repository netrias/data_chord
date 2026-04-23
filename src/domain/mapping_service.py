"""
Route column-to-CDE discovery through the Netrias recommendation API.

Axis of change: CDE recommendation service integration and response normalization.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from netrias_client import AlternativeEntry, ManifestPayload, NetriasClient

from src.domain.cde import ModelSuggestion
from src.domain.harmonize import normalize_manifest
from src.domain.manifest import HARMONIZATION_VALUES

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
    ) -> tuple[dict[str, list[ModelSuggestion]], ManifestPayload]:
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
        return cde_targets, manifest


def _cde_targets_from_manifest(
    manifest: ManifestPayload,
) -> dict[str, list[ModelSuggestion]]:
    """AnalyzeResponse needs per-column suggestions for frontend display."""
    # normalize_manifest guarantees list shape; dict fallback removed post-0.4.0
    return _targets_from_list_manifest(manifest["column_mappings"])


def _targets_from_list_manifest(
    entries: Sequence[object],
) -> dict[str, list[ModelSuggestion]]:
    """List index becomes the dict key for Stage 2 display.

    Column names can repeat in valid CSVs, so the transport key must be the stable
    column position rather than the header text.
    """
    targets: dict[str, list[ModelSuggestion]] = {}
    for column_id, entry in enumerate(entries):
        if entry is None or not isinstance(entry, dict):
            continue
        column_name = entry.get("column_name")
        if not isinstance(column_name, str):
            continue
        suggestions = _suggestions_from_alternatives(entry.get("alternatives", []))
        if suggestions:
            targets[str(column_id)] = suggestions
    return targets


def _suggestions_from_alternatives(
    alternatives: object,
) -> list[ModelSuggestion]:
    """External payload uses loose dicts; validate and convert to typed domain objects."""
    if not isinstance(alternatives, list):
        return []
    suggestions: list[ModelSuggestion] = []
    for alt in alternatives:
        alt_entry: AlternativeEntry = alt  # type: ignore[assignment]
        target = alt_entry.get("target")
        if not isinstance(target, str) or not target:
            continue
        raw_confidence = alt_entry.get("confidence")
        score = float(raw_confidence) if isinstance(raw_confidence, (int, float)) else 0.0
        # SDK boundary guarantees harmonization presence; ValueError here is defense-in-depth.
        raw_harmonization = alt_entry.get("harmonization")
        if raw_harmonization not in HARMONIZATION_VALUES:
            raise ValueError(
                f"expected harmonization in {sorted(HARMONIZATION_VALUES)}, "
                f"found {raw_harmonization!r} for target {target!r}"
            )
        suggestions.append(ModelSuggestion(target=target, confidence=score, harmonization=raw_harmonization))
    return suggestions
