"""End-to-end evidence for bug hunt findings 1, 3 and 16 on main."""

import csv
import io
import json
import re
import zipfile
from dataclasses import dataclass, replace
from datetime import datetime

import pytest
from openpyxl import load_workbook

import src.app.dependencies as deps
from src.domain.harmonization import MatchFidelity
from src.domain.harmonization_cache import EmptyHarmonizationCache
from src.integrations.dynamodb_harmonization_cache import DynamoDbHarmonizationCache
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
from tests.test_dynamodb_harmonization_cache import _Table
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
            if "negative" in request.context:
                value = "No"
            elif "positive" in request.context:
                value = "Yes"
            else:
                value = "Lung Cancer"
            responses.append(TermHarmonizationResponse(value, MatchFidelity.STRONG))
        return tuple(responses)


def _install(provider, monkeypatch, cache=None):
    monkeypatch.setattr(deps, "_harmonize_service", FileHarmonizationService(provider, cache=cache))
    monkeypatch.setattr(deps, "_harmonization_job_service", None)


def _allow(values):
    repository = deps.get_reference_data_repository()
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
async def test_01_excel(app_client, monkeypatch, native, mapped):
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
    mappings = {f"col_{i:04d}": None for i in range(5)}
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
    print(f"01 native={native} mapped={mapped} source={source!r} stage3={stage3!r} stage5={stage5!r}")
    assert stage3["E2"] == stage5["E2"] == expected["E2"]
    assert [stage3, stage5] == [expected, expected]
    if native:
        for workbook_bytes in [stage3_bytes, package.data]:
            book = load_workbook(io.BytesIO(workbook_bytes), data_only=True)
            assert book["Sample"]["D2"].value == 13
            book.close()


async def _eval_header(client, header):
    result = await _run(
        client, create_csv_content([[header], ["1"]]), "code.csv", TEST_CSV_CONTENT_TYPE, {"col_0000": TARGET}
    )
    return [list(csv.reader(io.StringIO(result.data.decode())))[1][0], result.manifest[0]["active_values"]["1"]]


@pytest.mark.parametrize(
    "first,second", [("positive", "negative"), ("negative", "positive")], ids=["positive-first", "negative-first"]
)
async def test_03_cache_equivalence(app_client, monkeypatch, first, second):
    # Given known distinct uncached file and audit answers for identical terms under opposite headers.
    _allow(["Yes", "No"])
    provider = ProviderMock()
    _install(provider, monkeypatch, EmptyHarmonizationCache())
    cold = {name: await _eval_header(app_client, f"1 means {name}") for name in (first, second)}
    assert cold == {"negative": ["No", "No"], "positive": ["Yes", "Yes"]}
    await deps.shutdown_harmonization_jobs()
    _install(provider, monkeypatch, DynamoDbHarmonizationCache(_Table()))
    # When one context warms the cache and two more files reuse it.
    warm_first = await _eval_header(app_client, f"1 means {first}")
    calls = len(provider.contexts)
    identical = await _eval_header(app_client, f"1 means {first}")
    assert len(provider.contexts) == calls
    opposite = await _eval_header(app_client, f"1 means {second}")
    # Then a same-context cache hit and an opposite-context lookup agree with uncached outputs.
    assert warm_first == identical == cold[first]
    print(f"03 order={first},{second} cold={cold} identical={identical} opposite={opposite} calls={provider.contexts}")
    assert opposite == cold[second]


@pytest.mark.parametrize("value", ["Other", " Other "], ids=["unpadded-control", "padded"])
async def test_16_whitespace(app_client, monkeypatch, value):
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
    print(f"16 source={value!r} actual={actual!r} provider_calls={len(provider.contexts)}")
    assert provider.contexts == []
    assert actual == [value, value, value, False, True, value, 0, 0]
