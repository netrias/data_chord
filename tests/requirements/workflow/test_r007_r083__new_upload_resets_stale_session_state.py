"""Requirement tests for starting a fresh workflow through upload."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from src.domain.data_model_cache import get_session_cache
from tests.cache_helpers import set_cache_pvs
from tests.requirements.helpers import CANONICAL_DIAGNOSIS, CSV_MIME_TYPE, DIAGNOSIS_COLUMN, single_column_csv

pytestmark = pytest.mark.asyncio


@pytest.mark.requirements("R-007", "R-083")
async def test_r007_r083__new_upload_clears_stale_pv_state(app_client: AsyncClient) -> None:
    """
    Given: A prior workflow has cached permissible values.
    When: The user uploads a new source CSV through Stage 1.
    Then: The stale cached permissible values are cleared before the new workflow continues.
    """
    # Given
    stale_cache = get_session_cache("old-workflow")
    set_cache_pvs(stale_cache, "old_cde", frozenset(["Old Value"]))
    assert stale_cache.has_any_pvs()

    # When
    response = await app_client.post(
        "/stage-1/upload",
        files={
            "file": (
                "new.csv",
                single_column_csv(DIAGNOSIS_COLUMN, CANONICAL_DIAGNOSIS),
                CSV_MIME_TYPE,
            )
        },
    )

    # Then
    assert response.status_code == 201
    assert not get_session_cache("old-workflow").has_any_pvs()
