"""Feature proof for the versioned programmatic recommendation API."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from httpx import AsyncClient

import src.app.dependencies as dependencies
from backend.app.error_handlers import GENERIC_API_ERROR_DETAIL
from src.auth.user_context import current_user_context
from src.domain.cde_recommendation import ProfiledColumn
from src.domain.manifest import (
    ColumnMappingManifest,
    ColumnMappingRecord,
    MappingAlternative,
)
from src.domain.reference_data import ReferenceModel

pytestmark = pytest.mark.asyncio

_API_KEY = "test-programmatic-api-key-32-bytes"
_REQUEST = {
    "data_model_key": "gc",
    "external_version_number": "11.0.4",
    "columns": [
        {"column_name": "diagnosis", "values": ["Lung", "", "Lung"]},
        {"column_name": "", "values": []},
    ],
    "top_k": 7,
}


class _RecordingRecommender:
    def __init__(self) -> None:
        self.calls: list[tuple[Sequence[ProfiledColumn], ReferenceModel, int, str]] = []

    async def recommend(
        self,
        columns: Sequence[ProfiledColumn],
        reference_model: ReferenceModel,
        *,
        top_k: int = 5,
    ) -> ColumnMappingManifest:
        self.calls.append((columns, reference_model, top_k, current_user_context().user_id))
        first = columns[0].identity
        return ColumnMappingManifest({
            first.key: ColumnMappingRecord(
                column_key=first.key,
                column_name=first.header,
                cde_key="primary_diagnosis",
                cde_id=42,
                harmonization="harmonizable",
                alternatives=(
                    MappingAlternative(
                        target="primary_diagnosis",
                        confidence=0.91,
                        cde_id=42,
                        harmonization="harmonizable",
                    ),
                ),
            )
        })


async def test_recommendation_api_preserves_position_and_uses_service_identity(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one valid API key and a recording form of the shared recommender.
    recommender = _RecordingRecommender()
    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)
    monkeypatch.setattr(dependencies, "_cde_recommender", recommender)

    # When: the pinned client request shape asks for seven results per column.
    response = await app_client.post(
        "/api/v1/recommend",
        headers={"x-api-key": _API_KEY},
        json=_REQUEST,
    )

    # Then: the shared engine receives exact profiles and the response preserves every position.
    assert response.status_code == 200
    assert response.json() == {
        "target_schema": "gc",
        "results": [
            {
                "column_name": "diagnosis",
                "matches": [
                    {
                        "target": "primary_diagnosis",
                        "target_cde_id": 42,
                        "confidence": 0.91,
                        "harmonization": "harmonizable",
                    }
                ],
            },
            {"column_name": "", "matches": []},
        ],
    }
    assert len(recommender.calls) == 1
    columns, _reference_model, top_k, user_id = recommender.calls[0]
    assert [str(column.identity.key) for column in columns] == ["col_0000", "col_0001"]
    assert [column.identity.header for column in columns] == ["diagnosis", ""]
    assert columns[0].profile.total_rows == 3
    assert columns[0].profile.null_count == 1
    assert [(value.value, value.count) for value in columns[0].profile.distinct_values] == [("Lung", 2)]
    assert top_k == 7
    assert user_id == "programmatic-api"


@pytest.mark.parametrize(
    ("headers", "expected_status"),
    [
        ({}, 401),
        ({"x-api-key": "wrong-key"}, 401),
        ([
            ("x-api-key", _API_KEY),
            ("x-api-key", _API_KEY),
        ], 401),
    ],
)
async def test_recommendation_api_rejects_missing_wrong_or_duplicate_key(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str] | list[tuple[str, str]],
    expected_status: int,
) -> None:
    # Given: the programmatic API has one configured key.
    recommender = _RecordingRecommender()
    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)
    monkeypatch.setattr(dependencies, "_cde_recommender", recommender)

    # When: authentication has a missing, wrong, or ambiguous key.
    response = await app_client.post("/api/v1/recommend", headers=headers, json=_REQUEST)

    # Then: authentication stops before recommendation work.
    assert response.status_code == expected_status
    assert response.json() == {"detail": "Invalid API key."}
    assert recommender.calls == []


async def test_recommendation_api_reports_missing_server_configuration(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: no API key is configured on the image.
    monkeypatch.delenv("DATA_CHORD_API_KEY", raising=False)

    # When: a programmatic request reaches the application.
    response = await app_client.post("/api/v1/recommend", json=_REQUEST)

    # Then: the service reports an operator configuration error.
    assert response.status_code == 503
    assert response.json() == {"detail": "Programmatic API is not configured."}


@pytest.mark.parametrize("top_k", [0, 11])
async def test_recommendation_api_rejects_top_k_outside_engine_limits(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    top_k: int,
) -> None:
    # Given: valid authentication and a request outside the engine's one-to-ten limit.
    recommender = _RecordingRecommender()
    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)
    monkeypatch.setattr(dependencies, "_cde_recommender", recommender)
    request = {**_REQUEST, "top_k": top_k}

    # When: the API validates the request.
    response = await app_client.post(
        "/api/v1/recommend",
        headers={"x-api-key": _API_KEY},
        json=request,
    )

    # Then: it returns a safe client error before reference or provider work.
    assert response.status_code == 422
    assert response.json() == {"detail": GENERIC_API_ERROR_DETAIL}
    assert recommender.calls == []


async def test_recommendation_api_rejects_excessive_empty_samples(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a request exceeds the total value count with zero-length strings.
    recommender = _RecordingRecommender()
    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)
    monkeypatch.setattr(dependencies, "_cde_recommender", recommender)
    request = {
        **_REQUEST,
        "columns": [
            {"column_name": f"column-{index}", "values": [""] * 5000}
            for index in range(6)
        ],
    }

    # When: the API validates the structural request size.
    response = await app_client.post(
        "/api/v1/recommend",
        headers={"x-api-key": _API_KEY},
        json=request,
    )

    # Then: count limits stop work even though the character limit is not reached.
    assert response.status_code == 422
    assert response.json() == {"detail": GENERIC_API_ERROR_DETAIL}
    assert recommender.calls == []


async def test_recommendation_api_has_no_unversioned_alias(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the versioned recommendation API is configured.
    recommender = _RecordingRecommender()
    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)
    monkeypatch.setattr(dependencies, "_cde_recommender", recommender)

    # When: a caller uses the old unversioned path.
    response = await app_client.post(
        "/recommend",
        headers={"x-api-key": _API_KEY},
        json=_REQUEST,
    )

    # Then: no compatibility alias exists and no recommendation work starts.
    assert response.status_code == 404
    assert recommender.calls == []


async def test_recommendation_api_rejects_raw_body_before_json_parsing(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: valid authentication and a raw body larger than the API byte limit.
    recommender = _RecordingRecommender()
    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)
    monkeypatch.setattr(dependencies, "_cde_recommender", recommender)
    oversized_body = b"{" + (b" " * (2 * 1024 * 1024))

    # When: the caller submits the oversized body.
    response = await app_client.post(
        "/api/v1/recommend",
        headers={
            "content-type": "application/json",
            "x-api-key": _API_KEY,
        },
        content=oversized_body,
    )

    # Then: byte limits reject it before JSON or recommendation work.
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body is too large."}
    assert recommender.calls == []


async def test_recommendation_api_authenticates_before_reading_raw_body(
    app_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an invalid key and a body that is larger than the API byte limit.
    recommender = _RecordingRecommender()
    monkeypatch.setenv("DATA_CHORD_API_KEY", _API_KEY)
    monkeypatch.setattr(dependencies, "_cde_recommender", recommender)
    oversized_body = b"{" + (b" " * (2 * 1024 * 1024))

    # When: authentication inspects the request before the body-limit middleware.
    response = await app_client.post(
        "/api/v1/recommend",
        headers={
            "content-type": "application/json",
            "x-api-key": "wrong-key",
        },
        content=oversized_body,
    )

    # Then: authentication rejects the request without starting API work.
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid API key."}
    assert recommender.calls == []
