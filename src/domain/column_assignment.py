"""
Canonical resolved column-to-CDE assignments for PV validation and review routing.

Changes when: column identity, assignment snapshot shape, or manifest/override
resolution rules change.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypedDict

from netrias_client import ManifestPayload

from src.domain.cde import NO_MAPPING_SENTINEL


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
    manifest_lookup = _build_manifest_lookup(manifest)
    assignments: dict[int, ColumnAssignment] = {}
    for column_id, column_name in enumerate(csv_headers):
        cde_key = _resolve_cde_key(column_id, manual_overrides, manifest_lookup)
        assignments[column_id] = ColumnAssignment(
            column_id=column_id,
            column_name=column_name,
            cde_key=cde_key,
        )
    return assignments


def _build_manifest_lookup(
    manifest: ManifestPayload | None,
) -> dict[int, str]:
    """Column_id → cde_key from the canonical list manifest."""
    if manifest is None:
        return {}
    column_mappings = manifest.get("column_mappings")
    if not isinstance(column_mappings, list):
        return {}
    return _lookup_from_list_manifest(column_mappings)


def _lookup_from_list_manifest(entries: Sequence[object]) -> dict[int, str]:
    """Array index = column_id; skip None entries."""
    result: dict[int, str] = {}
    for i, entry in enumerate(entries):
        cde_key = _get_cde_key(entry)
        if cde_key is not None:
            result[i] = cde_key
    return result


def _resolve_cde_key(
    column_id: int,
    manual_overrides: dict[int, str],
    manifest_lookup: dict[int, str],
) -> str | None:
    """Index overrides always win; sentinel means explicit No Mapping (don't fall through)."""
    if column_id in manual_overrides:
        override = manual_overrides[column_id]
        return None if override == NO_MAPPING_SENTINEL else override
    return manifest_lookup.get(column_id)


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


def extract_column_cde_mappings(manifest: ManifestPayload | None) -> dict[int, ColumnCdeMapping]:
    """Read cde_key from each canonical list entry; keyed by column_id (list index).

    Defense in depth: callers should already have a validated manifest, but we guard
    against non-list shapes to avoid silent empty results if called out of order.
    """
    if manifest is None:
        return {}
    column_mappings = manifest.get("column_mappings")
    if not isinstance(column_mappings, list):
        raise ValueError(
            f"extract_column_cde_mappings requires list-format column_mappings, "
            f"got {type(column_mappings).__name__}"
        )
    return _extract_from_list_manifest(column_mappings)


class ColumnCdeMapping(TypedDict):
    column_name: str
    cde_key: str


def _extract_from_list_manifest(entries: Sequence[object]) -> dict[int, ColumnCdeMapping]:
    """Each non-None entry carries column_name and cde_key directly."""
    result: dict[int, ColumnCdeMapping] = {}
    for idx, entry in enumerate(entries):
        if entry is None or not isinstance(entry, dict):
            continue
        column_name = entry.get("column_name")
        cde_key = entry.get("cde_key")
        if isinstance(column_name, str) and isinstance(cde_key, str):
            result[idx] = ColumnCdeMapping(column_name=column_name, cde_key=cde_key)
    return result


def _get_cde_key(entry: object) -> str | None:
    if not isinstance(entry, dict):
        return None
    cde_key = entry.get("cde_key")
    return cde_key if isinstance(cde_key, str) else None


__all__ = [
    "ColumnAssignment",
    "ColumnAssignmentSnapshot",
    "ColumnCdeMapping",
    "assignments_to_snapshots",
    "build_column_assignments",
    "extract_column_cde_mappings",
    "legacy_mappings_to_assignments",
    "snapshots_to_assignments",
]
