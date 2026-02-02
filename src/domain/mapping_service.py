"""
Discover column-to-CDE mappings and suggestions via the Netrias client.

Encapsulates integration with the external mapping discovery API and normalizes
varying response shapes into typed ModelSuggestion records.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.domain.cde import ModelSuggestion
from src.domain.demo_bypass import discover_bypass
from src.domain.manifest import ManifestPayload

logger = logging.getLogger(__name__)


class MappingDiscoveryService:
    """TEMPORARY DEMO BYPASS: hardcoded mappings until CDE ID API stabilizes.

    Production discovery + parsing helpers: see git 6039810.
    """

    def discover(
        self,
        *,
        csv_path: Path,
        target_schema: str,
    ) -> tuple[dict[str, list[ModelSuggestion]], dict[str, str], ManifestPayload]:
        """Hardcoded mappings guarantee demo reliability while CDE IDs are unstable."""
        return discover_bypass(csv_path, target_schema)
