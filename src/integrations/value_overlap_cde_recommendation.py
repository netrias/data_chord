"""Deterministic CDE recommender for local development and tests."""

from __future__ import annotations

from collections.abc import Sequence

from src.domain.cde_recommendation import ProfiledColumn
from src.domain.manifest import (
    ColumnMappingManifest,
    ColumnMappingRecord,
    MappingAlternative,
    RecommendationSource,
)
from src.domain.reference_data import ReferenceModel
from src.domain.value_overlap_mapping import suggest_value_overlap_mappings


class ValueOverlapCdeRecommender:
    """Keep the old deterministic behavior available only by explicit wiring."""

    async def recommend(
        self,
        columns: Sequence[ProfiledColumn],
        reference_model: ReferenceModel,
    ) -> ColumnMappingManifest:
        suggestions = suggest_value_overlap_mappings(
            {str(column.identity.key): column.profile for column in columns},
            reference_model.pvs,
        )
        records = {}
        for column_key, candidates in suggestions.by_column.items():
            top = candidates[0]
            cde = reference_model.catalog.get(top.cde_key)
            records[column_key] = ColumnMappingRecord(
                column_key=column_key,
                cde_key=top.cde_key,
                cde_id=cde.cde_id if cde else None,
                alternatives=tuple(
                    MappingAlternative(
                        target=candidate.cde_key,
                        confidence=candidate.overlap_ratio,
                    )
                    for candidate in candidates
                ),
                recommendation_source=RecommendationSource.VALUE_OVERLAP,
            )
        return ColumnMappingManifest(records)


__all__ = ["ValueOverlapCdeRecommender"]
