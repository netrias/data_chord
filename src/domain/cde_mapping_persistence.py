"""
Disk persistence for CDE column-mapping decisions.

Changes when: the CDE mapping document format changes or the storage backend changes.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from src.domain.cde import CDEMappingDocument, ColumnMappingDecision
from src.domain.dependencies import get_file_store
from src.domain.storage import FileType

_logger = logging.getLogger(__name__)


def save_cde_mapping(
    file_id: str,
    decisions: list[ColumnMappingDecision],
    schema_name: str,
    version_label: str | None,
) -> None:
    """Persist mapping decisions so Stage 5 can include them in the download zip."""
    ai_mapped = []
    user_overrides = []
    unmapped_columns = []

    for d in decisions:
        cde_name = d["cde_name"]
        if cde_name is None:
            unmapped_columns.append(d["column_name"])
        elif d["method"] == "user_override":
            user_overrides.append({
                "column_name": d["column_name"],
                "cde_name": cde_name,
                "cde_id": d["cde_id"],
                "cde_description": d["cde_description"],
            })
        else:
            ai_mapped.append({
                "column_name": d["column_name"],
                "cde_name": cde_name,
                "cde_id": d["cde_id"],
                "cde_description": d["cde_description"],
            })

    document: CDEMappingDocument = {
        "file_id": file_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "schema_name": schema_name,
        "version_label": version_label,
        "ai_mapped": ai_mapped,
        "user_overrides": user_overrides,
        "unmapped_columns": unmapped_columns,
    }
    store = get_file_store()
    store.save(file_id, FileType.COLUMN_MAPPING, document)
    _logger.info("Saved CDE mapping document", extra={"file_id": file_id, "column_count": len(decisions)})


def load_cde_mapping_json(file_id: str) -> str | None:
    """Return the CDE mapping as an indented JSON string, or None if not yet written."""
    store = get_file_store()
    data: dict[str, Any] | None = store.load(file_id, FileType.COLUMN_MAPPING)
    if data is None:
        return None
    return json.dumps(data, indent=2)


__all__ = ["save_cde_mapping", "load_cde_mapping_json"]
