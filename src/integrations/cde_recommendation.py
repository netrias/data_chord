"""Convert DataChord columns and reference data to CDE recommendation inputs."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from src.cde_recommend.candidate_ranker import CandidateRanker
from src.cde_recommend.recommendation_pipeline import match_columns_batch
from src.cde_recommend.result_cache import RecommendationCache
from src.cde_recommend.types import CDE, ColumnInput, ColumnResult
from src.domain.cde_recommendation import (
    ProfiledColumn,
    RecommendationUnavailableError,
)
from src.domain.column_profile import ColumnProfile
from src.domain.columns import ColumnKey
from src.domain.manifest import (
    ColumnMappingManifest,
    ColumnMappingRecord,
    MappingAlternative,
    RecommendationSource,
)
from src.domain.reference_data import ReferenceModel

logger = logging.getLogger(__name__)

_MAX_PROFILE_VALUES = 5000


class CdeRecommendationAdapter:
    """Use the CDE recommendation package behind DataChord domain types."""

    def __init__(
        self,
        ranker: CandidateRanker,
        cache: RecommendationCache,
        *,
        concurrency: int = 50,
    ) -> None:
        if concurrency < 1:
            raise ValueError("CDE recommendation concurrency must be positive")
        self._ranker = ranker
        self._cache = cache
        self._concurrency = concurrency

    async def recommend(
        self,
        columns: Sequence[ProfiledColumn],
        reference_model: ReferenceModel,
    ) -> ColumnMappingManifest:
        results = await match_columns_batch(
            columns=[
                ColumnInput(
                    column_name=column.identity.header,
                    column_values=_representative_values(column.profile),
                )
                for column in columns
            ],
            all_cdes=_cde_catalog(reference_model),
            ranker=self._ranker,
            cache=self._cache,
            data_model_key=reference_model.version.data_model_key,
            catalog_revision=reference_model.version.external_version_number,
            concurrency=self._concurrency,
        )
        if len(results) != len(columns):
            raise RecommendationUnavailableError(
                "CDE recommendation returned the wrong number of columns"
            )

        non_empty_results = [
            result
            for column, result in zip(columns, results, strict=True)
            if column.profile.distinct_values
        ]
        if non_empty_results and all(result.error is not None for result in non_empty_results):
            raise RecommendationUnavailableError("CDE recommendation provider is unavailable")

        records: dict[ColumnKey, ColumnMappingRecord] = {}
        for column, result in zip(columns, results, strict=True):
            if result.error is not None:
                logger.warning(
                    "CDE recommendation failed for one column",
                    extra={"column_key": str(column.identity.key), "error_code": result.error.code},
                )
                continue
            record = _mapping_record(column, result, reference_model)
            if record is not None:
                records[column.identity.key] = record
        return ColumnMappingManifest(records)


def _representative_values(profile: ColumnProfile) -> list[str]:
    total_values = sum(value.count for value in profile.distinct_values)
    if total_values <= _MAX_PROFILE_VALUES:
        return [
            value.value
            for value in profile.distinct_values
            for _ in range(value.count)
        ]
    weighted = [
        (
            value,
            value.count * _MAX_PROFILE_VALUES // total_values,
            value.count * _MAX_PROFILE_VALUES % total_values,
        )
        for value in profile.distinct_values
    ]
    remaining = _MAX_PROFILE_VALUES - sum(count for _value, count, _remainder in weighted)
    extra_values = {
        value.value
        for value, _count, _remainder in sorted(
            weighted,
            key=lambda item: (-item[2], item[0].value),
        )[:remaining]
    }
    return [
        value.value
        for value, count, _remainder in weighted
        for _ in range(count + (1 if value.value in extra_values else 0))
    ]


def _cde_catalog(reference_model: ReferenceModel) -> list[CDE]:
    return [
        CDE(
            cde_id=cde.cde_id,
            cde_key=cde.cde_key,
            pv_values=tuple(sorted(reference_model.pvs.get(cde.cde_key) or ())),
        )
        for cde in reference_model.catalog
    ]


def _mapping_record(
    column: ProfiledColumn,
    result: ColumnResult,
    reference_model: ReferenceModel,
) -> ColumnMappingRecord | None:
    if not result.matches:
        return None
    alternatives: list[MappingAlternative] = []
    for match in result.matches:
        cde = reference_model.catalog.get(match.cde_key)
        if cde is None:
            raise RecommendationUnavailableError(
                "CDE recommendation returned a target outside the reference model"
            )
        alternatives.append(
            MappingAlternative(
                target=cde.cde_key,
                confidence=match.confidence,
                cde_id=cde.cde_id,
                harmonization=match.harmonization.value,
            )
        )
    top = alternatives[0]
    return ColumnMappingRecord(
        column_key=column.identity.key,
        column_name=column.identity.header,
        cde_key=top.target,
        cde_id=top.cde_id,
        harmonization=top.harmonization,
        alternatives=tuple(alternatives),
        recommendation_source=RecommendationSource.AI,
    )


__all__ = ["CdeRecommendationAdapter"]
