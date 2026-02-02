"""
Hardcoded column-to-CDE mappings that bypass the Netrias discovery API.

Axis of change: demo-specific CDE mapping overrides.
Constraint: no production logic lives here; delete entirely when CDE ID API stabilizes.

The Netrias discovery API currently returns unstable CDE IDs that break
downstream PV lookup. This module provides hardcoded mappings to guarantee
demo reliability. Production discovery + parsing helpers: see git 6039810.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from src.domain.cde import CDEInfo, ModelSuggestion
from src.domain.config import get_data_model_key
from src.domain.data_model_cache import get_session_cache
from src.domain.data_model_client import DataModelClient, DataModelClientError
from src.domain.manifest import ColumnMappingEntry, ManifestPayload

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# HARDCODED CDE MAPPINGS
# -----------------------------------------------------------------------------
# Format: exact column_header -> (cde_key, cde_id)
# Keys use exact matching per domain rule (all character differences are significant).

DEMO_CDE_REGISTRY: dict[str, tuple[str, int]] = {
    # CDE ID 1: therapeutic_agents
    "therapeutic_agents": ("therapeutic_agents", 1),
    "therapeutic agents": ("therapeutic_agents", 1),
    # CDE ID 2: primary_diagnosis
    "primary_diagnosis": ("primary_diagnosis", 2),
    "primary diagnosis": ("primary_diagnosis", 2),
    "diagnosis": ("primary_diagnosis", 2),
    # CDE ID 3: morphology
    "morphology": ("morphology", 3),
    # CDE ID 4: tissue_or_organ_of_origin
    "tissue_or_organ_of_origin": ("tissue_or_organ_of_origin", 4),
    "tissue or organ of origin": ("tissue_or_organ_of_origin", 4),
    "site_of_origin": ("tissue_or_organ_of_origin", 4),
    # CDE ID 5: sample_anatomic_site
    "sample_anatomic_site": ("sample_anatomic_site", 5),
    "sample anatomic site": ("sample_anatomic_site", 5),
}


def _lookup_cde(column_header: str) -> tuple[str, int] | None:
    """Exact match preserves domain rule that all character differences are significant."""
    return DEMO_CDE_REGISTRY.get(column_header)


# -----------------------------------------------------------------------------
# BYPASS FUNCTIONS
# -----------------------------------------------------------------------------


def discover_bypass(
    csv_path: Path,
    target_schema: str,
) -> tuple[dict[str, list[ModelSuggestion]], dict[str, str], ManifestPayload]:
    """Netrias discovery API returns unstable CDE IDs; hardcoded mappings guarantee demo reliability."""
    logger.warning(
        "DEMO BYPASS ACTIVE: Using hardcoded CDE mappings instead of Netrias API",
        extra={"csv_path": str(csv_path), "target_schema": target_schema},
    )

    # Read CSV headers
    headers = _read_csv_headers(csv_path)

    cde_targets: dict[str, list[ModelSuggestion]] = {}
    column_mappings: dict[str, ColumnMappingEntry] = {}

    for header in headers:
        lookup = _lookup_cde(header)
        if lookup is None:
            # No hardcoded mapping - skip this column
            continue

        cde_key, cde_id = lookup

        # Build suggestion (high confidence since it's hardcoded)
        cde_targets[header] = [ModelSuggestion(target=cde_key, similarity=1.0)]

        # Build manifest entry
        column_mappings[header] = ColumnMappingEntry(targetField=cde_key, cde_id=cde_id)

    logger.info(
        "Demo bypass mapped columns",
        extra={
            "total_headers": len(headers),
            "mapped_columns": len(cde_targets),
            "unmapped": [h for h in headers if h not in cde_targets][:10],
        },
    )

    manifest: ManifestPayload = {"column_mappings": column_mappings}
    return cde_targets, {}, manifest


def get_demo_cde_infos(version_label: str) -> list[CDEInfo]:
    """Deduplicates registry entries since multiple column variants map to the same CDE."""
    # Deduplicate by cde_id since multiple column variants map to same CDE
    seen: dict[int, CDEInfo] = {}
    for cde_key, cde_id in DEMO_CDE_REGISTRY.values():
        if cde_id not in seen:
            seen[cde_id] = CDEInfo(
                cde_id=cde_id,
                cde_key=cde_key,
                description=f"Demo CDE: {cde_key}",
                version_label=version_label,
            )
    return list(seen.values())


def inject_demo_cdes_into_cache(file_id: str, client: DataModelClient) -> None:
    """PV fetching needs real model key + version; hardcoded CDEs avoid unstable discovery API."""
    data_model_key = get_data_model_key()
    try:
        version_label = client.get_latest_version(data_model_key)
    except DataModelClientError:
        logger.warning("Data Model Store API unavailable; defaulting to v1")
        version_label = "v1"

    cache = get_session_cache(file_id)
    demo_cdes = get_demo_cde_infos(version_label)
    cache.set_cdes(demo_cdes, data_model_key=data_model_key, version_label=version_label)

    logger.warning(
        "DEMO BYPASS: Injected hardcoded CDEs into session cache",
        extra={"file_id": file_id, "cde_count": len(demo_cdes), "data_model": data_model_key, "version": version_label},
    )


def _read_csv_headers(csv_path: Path) -> list[str]:
    """utf-8-sig strips BOM from Excel-exported CSVs that would corrupt the first header."""
    try:
        # Use utf-8-sig to automatically strip BOM (Byte Order Mark) from Excel CSVs
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            first_row = next(reader, None)
            return first_row if first_row else []
    except Exception as exc:
        logger.exception("Failed to read CSV headers", exc_info=exc)
        return []
