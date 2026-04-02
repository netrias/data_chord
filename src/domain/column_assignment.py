"""
Canonical resolved column-to-CDE assignments for PV validation and review routing.

Changes when: column identity, assignment snapshot shape, or manifest/override
resolution rules change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from src.domain.cde import NO_MAPPING_SENTINEL
from src.domain.manifest import ManifestPayload


@dataclass(frozen=True)
class ColumnAssignment:
    column_id: int
    column_name: str
    cde_key: str | None


class ColumnAssignmentSnapshot(TypedDict):
    column_id: int
    column_name: str
    cde_key: str | None


def build_column_assignments(
    manifest: ManifestPayload | None,
    manual_overrides: dict[int, str],
    csv_headers: list[str],
) -> dict[int, ColumnAssignment]:
    """PV lookup needs stable column identity even when headers repeat."""
    manifest_by_name = extract_column_cde_mappings(manifest)
    assignments: dict[int, ColumnAssignment] = {}
    for column_id, column_name in enumerate(csv_headers):
        cde_key = _resolve_cde_key(column_id, column_name, manual_overrides, manifest_by_name)
        assignments[column_id] = ColumnAssignment(
            column_id=column_id,
            column_name=column_name,
            cde_key=cde_key,
        )
    return assignments


def _resolve_cde_key(
    column_id: int,
    column_name: str,
    manual_overrides: dict[int, str],
    manifest_by_name: dict[str, str],
) -> str | None:
    """Index overrides always win; sentinel means explicit No Mapping (don't fall through)."""
    if column_id in manual_overrides:
        override = manual_overrides[column_id]
        return None if override == NO_MAPPING_SENTINEL else override
    return manifest_by_name.get(column_name)


def assignments_to_snapshots(assignments: dict[int, ColumnAssignment]) -> list[ColumnAssignmentSnapshot]:
    return [
        {
            "column_id": assignment.column_id,
            "column_name": assignment.column_name,
            "cde_key": assignment.cde_key,
        }
        for assignment in sorted(assignments.values(), key=lambda item: item.column_id)
    ]


def snapshots_to_assignments(payload: object) -> dict[int, ColumnAssignment]:
    if not isinstance(payload, list):
        return {}

    assignments: dict[int, ColumnAssignment] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        column_id = item.get("column_id")
        column_name = item.get("column_name")
        cde_key = item.get("cde_key")
        if not isinstance(column_id, int) or not isinstance(column_name, str):
            continue
        if cde_key is not None and not isinstance(cde_key, str):
            continue
        assignments[column_id] = ColumnAssignment(
            column_id=column_id,
            column_name=column_name,
            cde_key=cde_key,
        )
    return assignments


def legacy_mappings_to_assignments(payload: object) -> dict[int, ColumnAssignment]:
    if not isinstance(payload, dict):
        return {}

    assignments: dict[int, ColumnAssignment] = {}
    for key, value in payload.items():
        try:
            column_id = int(key)
        except (TypeError, ValueError):
            continue
        if not isinstance(value, str):
            continue
        assignments[column_id] = ColumnAssignment(
            column_id=column_id,
            column_name="",
            cde_key=value,
        )
    return assignments


def extract_column_cde_mappings(manifest: ManifestPayload | None) -> dict[str, str]:
    """Manifests come from external systems, so targetField may be absent."""
    if manifest is None:
        return {}
    column_mappings = manifest.get("column_mappings", {})
    return {
        column_name: target_field
        for column_name, entry in column_mappings.items()
        if (target_field := _get_target_field(entry)) is not None
    }


def _get_target_field(entry: object) -> str | None:
    if not isinstance(entry, dict):
        return None
    target_field = entry.get("targetField")
    return target_field if isinstance(target_field, str) else None


__all__ = [
    "ColumnAssignment",
    "ColumnAssignmentSnapshot",
    "assignments_to_snapshots",
    "build_column_assignments",
    "extract_column_cde_mappings",
    "legacy_mappings_to_assignments",
    "snapshots_to_assignments",
]
