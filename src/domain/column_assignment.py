"""
Canonical resolved column-to-CDE assignments for PV validation and review routing.

Changes when: column identity, assignment snapshot shape, or manifest/override
resolution rules change.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypedDict

from netrias_client import Harmonization, ManifestPayload

from src.domain.cde import NO_MAPPING_SENTINEL
from src.domain.manifest.models import HARMONIZATION_VALUES


@dataclass(frozen=True)
class ColumnAssignment:
    column_id: int
    column_name: str
    cde_key: str | None
    harmonization: Harmonization | None  # Co-null with cde_key: non-None iff cde_key is non-None.


class ColumnAssignmentSnapshot(TypedDict):
    column_id: int
    column_name: str
    cde_key: str | None
    harmonization: Harmonization | None


def build_column_assignments(
    manifest: ManifestPayload | None,
    manual_overrides: dict[int, str],
    csv_headers: list[str],
) -> dict[int, ColumnAssignment]:
    """PV lookup needs stable column identity even when headers repeat."""
    manifest_entries = _manifest_entries_by_column(manifest)
    harmonization_by_cde_key = _harmonization_lookup(manifest_entries)
    assignments: dict[int, ColumnAssignment] = {}
    for column_id, column_name in enumerate(csv_headers):
        cde_key = _resolve_cde_key(column_id, manual_overrides, manifest_entries)
        harmonization = _resolve_harmonization(cde_key, harmonization_by_cde_key)
        assignments[column_id] = ColumnAssignment(
            column_id=column_id,
            column_name=column_name,
            cde_key=cde_key,
            harmonization=harmonization,
        )
    return assignments


def _manifest_entries_by_column(
    manifest: ManifestPayload | None,
) -> dict[int, Mapping[str, object]]:
    """Column_id → raw manifest entry (dict) so harmonization + alternatives are available."""
    if manifest is None:
        return {}
    column_mappings = manifest.get("column_mappings")
    if not isinstance(column_mappings, list):
        return {}
    return {
        i: entry
        for i, entry in enumerate(column_mappings)
        if isinstance(entry, Mapping)
    }


def _resolve_cde_key(
    column_id: int,
    manual_overrides: dict[int, str],
    manifest_entries: dict[int, Mapping[str, object]],
) -> str | None:
    """Index overrides always win; sentinel means explicit No Mapping (don't fall through)."""
    if column_id in manual_overrides:
        override = manual_overrides[column_id]
        return None if override == NO_MAPPING_SENTINEL else override
    entry = manifest_entries.get(column_id)
    if entry is None:
        return None
    cde_key = entry.get("cde_key")
    return cde_key if isinstance(cde_key, str) else None


def _resolve_harmonization(
    cde_key: str | None,
    harmonization_by_cde_key: dict[str, Harmonization],
) -> Harmonization | None:
    """Co-null with cde_key. Harmonization is a property of the CDE itself, not the
    column position — so we look up the user's chosen cde_key in a cross-column map
    rather than reading the current column's manifest entry (which would incorrectly
    return the AI's original CDE harmonization when the user overrode to a different CDE).
    Defaults to "harmonizable" when the CDE doesn't appear anywhere in the manifest
    (e.g. user picks a CDE from the full schema list with no AI context anywhere).
    """
    if cde_key is None:
        return None
    return harmonization_by_cde_key.get(cde_key, "harmonizable")


def _harmonization_lookup(
    manifest_entries: dict[int, Mapping[str, object]],
) -> dict[str, Harmonization]:
    """Aggregate cde_key → harmonization across every entry and alternative in the manifest.
    Harmonization is a property of the CDE, so any occurrence is authoritative — later
    occurrences of the same cde_key should agree, and we let the last one win if not.
    """
    result: dict[str, Harmonization] = {}
    for entry in manifest_entries.values():
        _record_harmonization(result, entry.get("cde_key"), entry.get("harmonization"))
        alternatives = entry.get("alternatives")
        if not isinstance(alternatives, list):
            continue
        for alt in alternatives:
            if isinstance(alt, Mapping):
                _record_harmonization(result, alt.get("target"), alt.get("harmonization"))
    return result


def _record_harmonization(
    result: dict[str, Harmonization], cde_key: object, harmonization: object,
) -> None:
    if isinstance(cde_key, str) and harmonization in HARMONIZATION_VALUES:
        result[cde_key] = harmonization  # type: ignore[assignment]


def assignments_to_snapshots(assignments: dict[int, ColumnAssignment]) -> list[ColumnAssignmentSnapshot]:
    return [
        {
            "column_id": assignment.column_id,
            "column_name": assignment.column_name,
            "cde_key": assignment.cde_key,
            "harmonization": assignment.harmonization,
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
        harmonization = _coerce_snapshot_harmonization(item.get("harmonization"), cde_key)
        assignments[column_id] = ColumnAssignment(
            column_id=column_id,
            column_name=column_name,
            cde_key=cde_key,
            harmonization=harmonization,
        )
    return assignments


def _coerce_snapshot_harmonization(
    raw: object, cde_key: str | None,
) -> Harmonization | None:
    """Legacy snapshots predate the harmonization field: default to 'harmonizable' when a
    cde_key is present (conservative — normal harmonization for legacy sessions), None otherwise.
    """
    if raw in HARMONIZATION_VALUES:
        return raw  # type: ignore[return-value]
    if cde_key is None:
        return None
    return "harmonizable"


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
            harmonization="harmonizable",
        )
    return assignments


__all__ = [
    "ColumnAssignment",
    "ColumnAssignmentSnapshot",
    "assignments_to_snapshots",
    "build_column_assignments",
    "legacy_mappings_to_assignments",
    "snapshots_to_assignments",
]
