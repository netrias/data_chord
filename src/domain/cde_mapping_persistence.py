"""
Disk persistence for CDE column-mapping decisions.

Changes when: the CDE mapping document format changes or the storage backend changes.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, Literal

from src.domain.cde import CDEEntry, CDEMappingDocument, ColumnMappingDecision
from src.domain.column_assignment import ColumnAssignment
from src.domain.dependencies import get_file_store
from src.domain.manifest.models import PASS_THROUGH_HARMONIZATIONS
from src.domain.storage import FileType

_logger = logging.getLogger(__name__)


def save_cde_mapping(
    file_id: str,
    decisions: list[ColumnMappingDecision],
    assignments: dict[int, ColumnAssignment],
    schema_name: str,
    version_label: str | None,
) -> None:
    """Persist mapping decisions so Stage 5 can include them in the download zip.

    Routing rule applied by explicit decision column_id. Stale browser payloads
    without column_id fall back to positional pairing.
    Precedence: unmapped → pass_through → user_overrides → ai_mapped.
    """
    ai_mapped: list[CDEEntry] = []
    user_overrides: list[CDEEntry] = []
    pass_through: list[CDEEntry] = []
    unmapped_columns: list[str] = []

    for i, d in enumerate(decisions):
        assignment = _assignment_for_decision(d, i, assignments)
        _route_decision(d, assignment, ai_mapped, user_overrides, pass_through, unmapped_columns)

    document: CDEMappingDocument = {
        "file_id": file_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "schema_name": schema_name,
        "version_label": version_label,
        "ai_mapped": ai_mapped,
        "user_overrides": user_overrides,
        "pass_through": pass_through,
        "unmapped_columns": unmapped_columns,
    }
    store = get_file_store()
    store.save(file_id, FileType.COLUMN_MAPPING, document)
    _logger.info("Saved CDE mapping document", extra={"file_id": file_id, "column_count": len(decisions)})


def _assignment_for_decision(
    decision: ColumnMappingDecision,
    position: int,
    assignments: dict[int, ColumnAssignment],
) -> ColumnAssignment | None:
    column_id = decision.get("column_id")
    if isinstance(column_id, int):
        return assignments.get(column_id)
    return assignments.get(position)


def _route_decision(
    d: ColumnMappingDecision,
    assignment: ColumnAssignment | None,
    ai_mapped: list[CDEEntry],
    user_overrides: list[CDEEntry],
    pass_through: list[CDEEntry],
    unmapped_columns: list[str],
) -> None:
    """Apply the four-bucket routing rule to a single (decision, assignment) pair.

    Precedence order matters: cde_key=None wins over harmonization check so that
    explicit "No Mapping" decisions are never accidentally routed to pass_through.
    """
    cde_name = d["cde_name"]

    # Rule 1: no CDE assigned — explicit No Mapping or unresolved column.
    if cde_name is None or assignment is None or assignment.cde_key is None:
        unmapped_columns.append(d["column_name"])
        return

    entry = _build_entry(d, cde_name, d["method"])

    # Rule 2: non-harmonizable CDEs pass through without value transformation.
    if assignment.harmonization in PASS_THROUGH_HARMONIZATIONS:
        pass_through.append(entry)
        return

    # Rules 3 & 4: split by method for harmonizable columns.
    if d["method"] == "user_override":
        user_overrides.append(entry)
    else:
        ai_mapped.append(entry)


def _build_entry(
    d: ColumnMappingDecision,
    cde_name: str,
    method: Literal["ai_recommendation", "user_override"],
) -> CDEEntry:
    """Construct a CDEEntry from a decision and its resolved method."""
    return CDEEntry(
        column_name=d["column_name"],
        cde_name=cde_name,
        cde_id=d["cde_id"],
        cde_description=d["cde_description"],
        method=method,
    )


def load_cde_mapping_json(file_id: str) -> str | None:
    """Return the CDE mapping as an indented JSON string, or None if not yet written."""
    store = get_file_store()
    data: dict[str, Any] | None = store.load(file_id, FileType.COLUMN_MAPPING)
    if data is None:
        return None
    return json.dumps(data, indent=2)


__all__ = ["save_cde_mapping", "load_cde_mapping_json"]
