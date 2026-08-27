"""Build the shared harmonization summary from a stored manifest."""

from __future__ import annotations

from typing import NamedTuple

from src.domain.column_outcomes import (
    ColumnOutcome,
    FinalizedValueOutcome,
    FinalValueSource,
    summarize_column_outcomes,
)
from src.domain.columns import ColumnKey
from src.domain.harmonization import (
    HarmonizationColumnBreakdown,
    HarmonizationManifestSummary,
    MatchFidelity,
    MatchFidelityCount,
)
from src.domain.manifest import ManifestRow, ManifestSummary
from src.domain.pv_validation import check_value_conformance
from src.persistence.pv_manifest_store import ColumnPvSets


class _ColumnStats(NamedTuple):
    total_rows: int
    changed_rows: int
    unique_terms_changed: int
    non_conformant_terms: int
    match_fidelity_counts: dict[MatchFidelity, int]


def _compute_column_stats(
    col_rows: list[ManifestRow],
    pv_set: frozenset[str] | None,
) -> _ColumnStats:
    if not col_rows:
        return _ColumnStats(0, 0, 0, 0, {fidelity: 0 for fidelity in MatchFidelity})

    finalized_outcomes = [_finalized_value_outcome(row, pv_set) for row in col_rows]
    summary = summarize_column_outcomes(finalized_outcomes)[0]
    fidelity_counts: dict[MatchFidelity, int] = {fidelity: 0 for fidelity in MatchFidelity}
    for row, outcome in zip(col_rows, finalized_outcomes, strict=True):
        if outcome.is_changed:
            fidelity_counts[row.match_fidelity] += 1

    return _ColumnStats(
        summary.total_rows,
        summary.changed_rows,
        summary.changed_distinct_values,
        summary.non_conformant_distinct_values,
        fidelity_counts,
    )


def _effective_ai_value(row: ManifestRow) -> str:
    """Treat a blank provider result as the manifest's pass-through sentinel."""
    if not row.top_harmonization.strip():
        return row.to_harmonize
    return row.top_harmonization


def _finalized_value_outcome(
    row: ManifestRow,
    pv_set: frozenset[str] | None,
) -> FinalizedValueOutcome:
    final_value = _effective_ai_value(row)
    return FinalizedValueOutcome(
        column_key=row.column_key,
        source_column_index=row.column_id,
        column_label=row.column_name,
        original_value=row.to_harmonize,
        final_value=final_value,
        final_value_source=(
            FinalValueSource.DATA_CHORD
            if final_value != row.to_harmonize
            else FinalValueSource.SOURCE
        ),
        occurrence_count=len(row.row_indices) if row.row_indices else 1,
        pv_set_available=bool(pv_set),
        is_pv_conformant=check_value_conformance(final_value, pv_set),
    )


def _create_breakdown_schema(
    outcome: ColumnOutcome,
    col_rows: list[ManifestRow],
    pv_set: frozenset[str] | None,
) -> HarmonizationColumnBreakdown:
    stats = _compute_column_stats(col_rows, pv_set)
    return HarmonizationColumnBreakdown(
        column_name=outcome.column_label,
        label=outcome.column_label or "Unknown",
        column_key=str(outcome.column_key),
        source_column_index=outcome.source_column_index,
        review_status=outcome.review_status,
        total_rows=outcome.total_rows,
        changed_rows=outcome.changed_rows,
        unchanged_rows=outcome.total_rows - outcome.changed_rows,
        unique_terms=outcome.total_distinct_values,
        unique_terms_changed=outcome.changed_distinct_values,
        successfully_harmonized_terms=outcome.successfully_harmonized_distinct_values,
        unique_terms_unchanged=outcome.total_distinct_values - outcome.changed_distinct_values,
        non_conformant_terms=outcome.non_conformant_distinct_values,
        match_fidelity_counts_changed=[
            MatchFidelityCount(id=fidelity, label=fidelity.label, term_count=stats.match_fidelity_counts[fidelity])
            for fidelity in MatchFidelity
        ],
    )


def _build_column_breakdowns(
    rows: list[ManifestRow],
    column_pv_map: ColumnPvSets,
) -> list[HarmonizationColumnBreakdown]:
    column_rows: dict[ColumnKey, list[ManifestRow]] = {}
    for row in rows:
        column_rows.setdefault(row.column_key, []).append(row)

    outcomes = summarize_column_outcomes([
        _finalized_value_outcome(row, column_pv_map.get(row.column_key))
        for row in rows
    ])
    return [
        _create_breakdown_schema(
            outcome,
            column_rows[outcome.column_key],
            column_pv_map.get(outcome.column_key),
        )
        for outcome in outcomes
    ]


def build_harmonization_manifest_summary(
    manifest: ManifestSummary,
    column_pv_map: ColumnPvSets,
    *,
    source_file_name: str | None = None,
    reference_model_label: str | None = None,
    reference_model_version: str | None = None,
) -> HarmonizationManifestSummary:
    """Convert stored manifest rows into the summary returned by callers."""
    column_breakdowns = _build_column_breakdowns(manifest.rows, column_pv_map)
    total_non_conformant = sum(breakdown.non_conformant_terms for breakdown in column_breakdowns)
    fidelity_counts = {fidelity: 0 for fidelity in MatchFidelity}
    for row in manifest.rows:
        fidelity_counts[row.match_fidelity] += 1
    return HarmonizationManifestSummary(
        total_terms=manifest.total_terms,
        changed_terms=manifest.changed_terms,
        match_fidelity_counts=[
            MatchFidelityCount(id=fidelity, label=fidelity.label, term_count=fidelity_counts[fidelity])
            for fidelity in MatchFidelity
        ],
        non_conformant_terms=total_non_conformant,
        source_file_name=source_file_name,
        reference_model_label=reference_model_label,
        reference_model_version=reference_model_version,
        column_breakdowns=column_breakdowns,
    )


__all__ = ["build_harmonization_manifest_summary"]
