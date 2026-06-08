"""Column-to-CDE conformance summaries for Stage 2.

Axis of change: how a column's distinct values are summarized against each CDE
under that CDE's type. Owns both integer match counts and PV overlap ratios so
the frontend never recomputes conformance semantics.

Match-count output is sparse: only entries with a positive count appear. The
frontend treats a missing key as zero. Overlap-ratio output omits undefined
ratios, but includes PV CDEs with a real zero overlap as ``0.0``.
"""

from __future__ import annotations

from src.domain.cde import CdeType
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog


def column_value_overlap_ratio(
    distinct_values: frozenset[str],
    cde_type: CdeType,
    pv_set: frozenset[str] | None,
) -> float | None:
    """PV overlap is undefined for rename-only CDEs, unfetched PVs, and empty columns."""
    if cde_type != CdeType.PV:
        return None
    if pv_set is None:
        return None
    if not distinct_values:
        return None
    return len(distinct_values & pv_set) / len(distinct_values)


def compute_column_overlap_by_cde(
    distinct_values: frozenset[str],
    catalog: CdeCatalog,
    pv_sets: CdePvCatalog,
) -> dict[str, float]:
    """Build a per-CDE PV overlap map while preserving real zero-overlap results."""
    overlaps: dict[str, float] = {}
    for cde in catalog:
        ratio = column_value_overlap_ratio(
            distinct_values,
            cde.cde_type,
            pv_sets.get(cde.cde_key),
        )
        if ratio is not None:
            overlaps[cde.cde_key] = ratio
    return overlaps


def compute_match_counts(
    distinct_values: frozenset[str],
    catalog: CdeCatalog,
    pv_sets: CdePvCatalog,
) -> dict[str, int]:
    """For each CDE, count the column's distinct values that conform under its type.

    PV          → ``|distinct_values & pv_sets[cde_key]|``
    PASSTHROUGH → ``len(distinct_values)`` (everything passes through)

    ``pv_sets`` only needs entries for PV-typed CDEs; missing keys for PV CDEs
    contribute zero (and are dropped from the sparse output). This lets callers
    avoid fetching PVs for non-PV CDEs.
    """
    passthrough_count = len(distinct_values)
    out: dict[str, int] = {}
    for cde in catalog:
        match cde.cde_type:
            case CdeType.PV:
                pv_set = pv_sets.get(cde.cde_key)
                count = len(distinct_values & pv_set) if pv_set else 0
            case CdeType.PASSTHROUGH:
                count = passthrough_count
        if count > 0:
            out[cde.cde_key] = count
    return out
