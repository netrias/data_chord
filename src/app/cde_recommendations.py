"""Application use case for CDE recommendations from inline column samples."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from src.domain.cde_recommendation import CdeRecommender, ProfiledColumn
from src.domain.column_profile import build_column_profile
from src.domain.columns import ColumnIdentity, column_key_for_index
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.manifest import ColumnMappingManifest
from src.domain.reference_data import ReferenceDataRepository


@dataclass(frozen=True)
class RecommendationColumn:
    """One positional source column and its bounded sample values."""

    name: str
    values: tuple[str, ...]


async def recommend_cdes(
    *,
    columns: Sequence[RecommendationColumn],
    data_model_version: DataModelVersionReference,
    top_k: int,
    reference_data_repository: ReferenceDataRepository,
    recommender: CdeRecommender,
) -> ColumnMappingManifest:
    """Load one model and run the shared recommendation engine."""
    if not 1 <= top_k <= 10:
        raise ValueError("top_k must be between 1 and 10")
    reference_model = await asyncio.to_thread(
        reference_data_repository.load_model,
        data_model_version,
    )
    profiled_columns = [
        ProfiledColumn(
            identity=ColumnIdentity(column_key_for_index(index), column.name),
            profile=build_column_profile(column_key_for_index(index), column.values),
        )
        for index, column in enumerate(columns)
    ]
    return await recommender.recommend(
        profiled_columns,
        reference_model,
        top_k=top_k,
    )


__all__ = ["RecommendationColumn", "recommend_cdes"]
