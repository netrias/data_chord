"""Requirement tests for Stage 5 export pass-through and audit artifacts."""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest
from httpx import AsyncClient

from src.domain.cde import ColumnMappingDecision
from src.domain.cde_mapping_persistence import save_cde_mapping
from src.domain.column_assignment import ColumnAssignment
from src.domain.storage import HARMONIZED_SUFFIX, UploadStorage
from tests.conftest import upload_content
from tests.requirements.helpers import PRIMARY_DIAGNOSIS_CDE, UNTOUCHED_EXPORT_VALUE

pytestmark = pytest.mark.asyncio


@pytest.mark.requirements("R-020", "R-053", "R-054", "R-058")
async def test_r020_r053_r054_r058__export_preserves_unmapped_values_and_includes_mapping_artifact(
    app_client: AsyncClient,
    temp_storage: UploadStorage,
) -> None:
    """
    Given: A workflow has an unmapped column and a persisted column-mapping artifact.
    When: The user downloads the Stage 5 export bundle.
    Then: The CSV preserves unmapped values and the bundle includes a CDE mapping JSON artifact.
    """
    # Given
    content = f"mapped,unmapped\nHarmonized,{UNTOUCHED_EXPORT_VALUE}\n".encode()
    file_id = await upload_content(app_client, content, "audit.csv")
    meta = temp_storage.load(file_id)
    assert meta is not None
    harmonized_path = meta.saved_path.with_name(f"{meta.saved_path.stem}{HARMONIZED_SUFFIX}")
    harmonized_path.write_bytes(content)
    decisions: list[ColumnMappingDecision] = [
        {
            "column_id": 0,
            "column_name": "mapped",
            "cde_name": PRIMARY_DIAGNOSIS_CDE,
            "cde_id": 2,
            "cde_description": "Primary Diagnosis",
            "method": "ai_recommendation",
        },
        {
            "column_id": 1,
            "column_name": "unmapped",
            "cde_name": None,
            "cde_id": None,
            "cde_description": None,
            "method": "user_override",
        },
    ]
    assignments = {
        0: ColumnAssignment(0, "mapped", PRIMARY_DIAGNOSIS_CDE, "harmonizable"),
        1: ColumnAssignment(1, "unmapped", None, None),
    }
    save_cde_mapping(file_id, decisions, assignments, "CCDI", "1")
    assert UNTOUCHED_EXPORT_VALUE.encode() in content

    # When
    response = await app_client.post("/stage-5/download", json={"file_id": file_id})

    # Then
    assert response.status_code == 200
    with zipfile.ZipFile(BytesIO(response.content), "r") as zf:
        names = zf.namelist()
        csv_name = next(name for name in names if name.endswith(".csv"))
        mapping_name = next(name for name in names if name.endswith("_cde_mapping.json"))
        assert UNTOUCHED_EXPORT_VALUE in zf.read(csv_name).decode("utf-8")
        assert '"unmapped_columns"' in zf.read(mapping_name).decode("utf-8")
