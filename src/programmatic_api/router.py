"""HTTP adapter for the versioned programmatic API."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, HTTPException, status

import src.app.dependencies as dependencies
from src.app.cde_recommendations import RecommendationColumn, recommend_cdes
from src.domain.cde_recommendation import RecommendationUnavailableError
from src.domain.columns import column_key_for_index
from src.domain.data_model_version_reference import DataModelVersionReference
from src.domain.manifest import ColumnMappingManifest, MappingAlternative
from src.domain.reference_data import ReferenceDataError
from src.programmatic_api.harmonization_router import harmonization_router
from src.programmatic_api.schemas import (
    HarmonizationKind,
    RecommendationColumnResponse,
    RecommendationMatchResponse,
    RecommendationRequest,
    RecommendationResponse,
)

programmatic_api_router = APIRouter(prefix="/api/v1", tags=["Programmatic API v1"])
programmatic_api_router.include_router(harmonization_router)


@programmatic_api_router.post(
    "/recommend",
    response_model=RecommendationResponse,
    response_model_exclude_none=True,
)
async def recommend_columns(payload: RecommendationRequest) -> RecommendationResponse:
    try:
        manifest = await recommend_cdes(
            columns=[
                RecommendationColumn(column.column_name, tuple(column.values))
                for column in payload.columns
            ],
            data_model_version=DataModelVersionReference(
                payload.data_model_key,
                payload.external_version_number,
            ),
            top_k=payload.top_k,
            reference_data_repository=dependencies.get_reference_data_repository(),
            recommender=dependencies.get_cde_recommender(),
        )
        return _response_from_manifest(payload, manifest)
    except ReferenceDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Reference data is currently unavailable.",
        ) from exc
    except RecommendationUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CDE recommendations are currently unavailable.",
        ) from exc


def _response_from_manifest(
    request: RecommendationRequest,
    manifest: ColumnMappingManifest,
) -> RecommendationResponse:
    results = []
    for index, column in enumerate(request.columns):
        record = manifest.records.get(column_key_for_index(index))
        alternatives = () if record is None else record.alternatives
        if record is not None and not alternatives:
            alternatives = (
                MappingAlternative(
                    target=record.cde_key,
                    confidence=1.0,
                    cde_id=record.cde_id,
                    harmonization=record.harmonization,
                ),
            )
        results.append(
            RecommendationColumnResponse(
                column_name=column.column_name,
                matches=[
                    RecommendationMatchResponse(
                        target=alternative.target,
                        target_cde_id=alternative.cde_id,
                        confidence=alternative.confidence,
                        harmonization=_harmonization_kind(alternative.harmonization),
                    )
                    for alternative in alternatives[: request.top_k]
                ],
            )
        )
    return RecommendationResponse(target_schema=request.data_model_key, results=results)


def _harmonization_kind(value: str | None) -> HarmonizationKind:
    resolved = value or "harmonizable"
    if resolved not in {"harmonizable", "no_permissible_values", "numeric"}:
        raise RecommendationUnavailableError("CDE recommendation returned invalid harmonization metadata")
    return cast(HarmonizationKind, resolved)


__all__ = ["programmatic_api_router"]
