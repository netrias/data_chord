"""Preservation of native Excel values, prediction context and exact audit text."""

import csv
import io
import json
import re
import zipfile
from dataclasses import dataclass, replace
from datetime import datetime
from unittest.mock import Mock

import pytest
from openpyxl import load_workbook

import src.app.dependencies as deps
from src.domain.harmonization import MatchFidelity
from src.integrations.harmonize import FileHarmonizationService, TermHarmonizationResponse
from src.storage import WorkflowFile
from tests.conftest import (
    TEST_CSV_CONTENT_TYPE,
    TEST_TARGET_EXTERNAL_VERSION_NUMBER,
    TEST_TARGET_SCHEMA,
    TEST_XLSX_CONTENT_TYPE,
    confirm_mapping_choices,
    create_csv_content,
    create_xlsx_content,
    upload_content,
)
from tests.test_stage_1_to_3_features import _wait_for_stage_three_job

pytestmark = pytest.mark.asyncio
TARGET = "primary_diagnosis"


class ProviderMock:
    def __init__(self):
        self.contexts = []

    def harmonize(self, requests):
        responses = []
        for request in requests:
            self.contexts.append(request.context)
            responses.append(TermHarmonizationResponse("Lung Cancer", MatchFidelity.STRONG))
        return tuple(responses)


def _install(provider, monkeypatch):
    monkeypatch.setattr(deps, "_harmonize_service", FileHarmonizationService(provider))
    monkeypatch.setattr(deps, "_harmonization_job_service", None)


def _allow(values):
    repository = deps.get_reference_data_repository()
    assert isinstance(repository, Mock)
    previous = repository.load_model.return_value
    repository.load_model.return_value = replace(previous, pvs=previous.pvs.with_values({TARGET: frozenset(values)}))


@dataclass
class Download:
    file_id: str
    data: bytes
    manifest: list
    summary: dict
    rows: dict


async def _run(client, content, filename, content_type, mappings):
    file_id = await upload_content(client, content, filename, content_type)
    analyzed = await client.post(
        "/stage-1/analyze",
        json={
            "file_id": file_id,
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": TEST_TARGET_EXTERNAL_VERSION_NUMBER,
        },
    )
    assert analyzed.status_code == 200, analyzed.text
    await confirm_mapping_choices(client, file_id, manual_overrides=mappings)
    start = await client.post("/stage-3/harmonize", json={"file_id": file_id})
    assert start.status_code == 200, start.text
    job = start.json()
    if job["status"] != "succeeded":
        job = await _wait_for_stage_three_job(client, job["job_id"], file_id)
    assert job["status"] == "succeeded", job
    results = []
    for endpoint in ("/stage-4/rows", "/stage-5/summary", "/stage-5/download"):
        response = await client.post(endpoint, json={"file_id": file_id})
        assert response.status_code == 200, response.text[:300]
        results.append(response)
    with zipfile.ZipFile(io.BytesIO(results[2].content)) as archive:
        names = archive.namelist()
        assert len(names) == 3, names
        data = archive.read(next(n for n in names if n.endswith(filename[filename.rindex(".") :])))
        audit = json.loads(archive.read(next(n for n in names if n.endswith("_manifest.json"))))
    return Download(file_id, data, audit, results[1].json(), results[0].json())


def _native_cells(content):
    workbook = load_workbook(io.BytesIO(content), data_only=False)
    try:
        return {c.coordinate: [c.data_type, c.value] for c in workbook["Sample"][2]}
    finally:
        workbook.close()


@pytest.mark.parametrize(
    "native,mapped",
    [(False, False), (True, False), (True, True)],
    ids=["text-control", "native-no-op", "native-with-target-change"],
)
async def test_excel_export_preserves_untouched_native_values(app_client, monkeypatch, native, mapped):
    # Given independently readable source cells and confirmed unmapped source positions.
    _install(ProviderMock(), monkeypatch)
    values = [12, True, datetime(2024, 1, 2), "=A2+1"] if native else ["12", "true", "2024-01-02", "A2+1"]
    content = create_xlsx_content(
        {"Sample": [["number", "flag", "date", "formula", TARGET], [*values, "raw diagnosis"]]}
    )
    if native:
        # Excel supplies the cached result in addition to the formula expression.
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(content)) as original, zipfile.ZipFile(output, "w") as updated:
            for member in original.infolist():
                data = original.read(member)
                if member.filename == "xl/worksheets/sheet1.xml":
                    data = re.sub(rb"<f>A2\+1</f><v(?:\s*/>|></v>)", b"<f>A2+1</f><v>13</v>", data)
                    assert b"<v>13</v>" in data
                    # OOXML allows consecutive row and cell positions to be implicit.
                    data = re.sub(rb'(<(?:row|c)) r="(?:[A-Z]+)?[0-9]+"', rb'\1', data)
                updated.writestr(member, data)
        content = output.getvalue()
    source = _native_cells(content)
    if native:
        assert source["D2"] == ["f", "=A2+1"]
    # When the application harmonizes the worksheet and exports its ZIP.
    mappings: dict[str, str | None] = {f"col_{i:04d}": None for i in range(5)}
    mappings["col_0004"] = TARGET if mapped else None
    package = await _run(app_client, content, "sample.xlsx", TEST_XLSX_CONTENT_TYPE, mappings)
    with deps.get_workflow_storage().materialize_artifact(
        deps.get_user_context(), package.file_id, WorkflowFile.HARMONIZED_OUTPUT
    ) as file:
        stage3_bytes = file.read_bytes()
        stage3 = _native_cells(stage3_bytes)
    stage5 = _native_cells(package.data)
    # Then both artifacts preserve every untouched cell and the intended mapped value.
    expected = dict(source)
    if mapped:
        expected["E2"] = ["s", "Lung Cancer"]

    assert stage3["E2"] == stage5["E2"] == expected["E2"]
    assert [stage3, stage5] == [expected, expected]
    if native:
        for workbook_bytes in [stage3_bytes, package.data]:
            book = load_workbook(io.BytesIO(workbook_bytes), data_only=True)
            assert book["Sample"]["D2"].value == 13
            book.close()


@pytest.mark.parametrize("value", ["Other", " Other "], ids=["unpadded-control", "padded"])
async def test_exact_value_whitespace_survives_export_and_audit(app_client, monkeypatch, value):
    # Given an approved exact value, including its significant whitespace.
    _allow([value])
    provider = ProviderMock()
    _install(provider, monkeypatch)
    # When the row is uploaded, harmonized, reviewed and downloaded.
    package = await _run(
        app_client,
        create_csv_content([[TARGET], [value]]),
        "whitespace.csv",
        TEST_CSV_CONTENT_TYPE,
        {"col_0000": TARGET},
    )
    # Then the exact-match cell, review and audit all show the same unchanged, conformant value.
    output = list(csv.reader(io.StringIO(package.data.decode())))[1][0]
    review = package.rows["columns"][0]["transformations"][0]
    summary = package.summary["column_summaries"][0]
    actual = [
        output,
        review["originalValue"],
        review["harmonizedValue"],
        review["isChanged"],
        review["isPVConformant"],
        package.manifest[0]["active_values"]["1"],
        summary["changed_rows"],
        summary["non_conformant_values"],
    ]

    assert provider.contexts == []
    assert actual == [value, value, value, False, True, value, 0, 0]
