"""Feature tests for request-scoped workflow ownership."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from src.domain.user_context import ALB_IDENTITY_HEADER
from tests.conftest import TEST_CSV_CONTENT_TYPE, TEST_TARGET_SCHEMA

pytestmark = pytest.mark.asyncio


async def test_alb_identity_header_controls_workflow_ownership(
    app_client: AsyncClient,
    sample_csv_path: Path,
) -> None:
    # Given: Alice uploaded a workflow and Bob has not been granted access
    upload_response = await app_client.post(
        "/stage-1/upload",
        headers={ALB_IDENTITY_HEADER: "alice"},
        files={"file": (sample_csv_path.name, sample_csv_path.read_bytes(), TEST_CSV_CONTENT_TYPE)},
    )
    assert upload_response.status_code == 201
    file_id = upload_response.json()["file_id"]

    # When: Bob tries to analyze Alice's upload by guessing the file id
    response = await app_client.post(
        "/stage-1/analyze",
        headers={ALB_IDENTITY_HEADER: "bob"},
        json={"file_id": file_id, "target_schema": TEST_TARGET_SCHEMA},
    )

    # Then: workflow storage denies access before the file is processed
    assert response.status_code == 403
