from dataclasses import replace
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from lxml import etree
from netrias_client import read_tabular
from openpyxl import load_workbook

from src.persistence.tabular_output import write_tabular_changes
from tests.conftest import (
    TEST_XLSX_CONTENT_TYPE,
    create_test_manifest_parquet,
    create_xlsx_content,
    review_state_payload,
    store_test_completed_harmonization,
    upload_content,
)
from tests.review_package import ReviewPackage


def _shared_workbook(tmp_path: Path) -> Path:
    original = create_xlsx_content({"Keep": [["other"]], "Data": [["Input", "Formula"], [3, "=A2+1"], [4, "=A3+1"]]})
    source = tmp_path / "shared.xlsx"
    with ZipFile(BytesIO(original)) as workbook, ZipFile(source, "w") as fixture:
        for part in workbook.infolist():
            data = workbook.read(part)
            if part.filename == "xl/worksheets/sheet2.xml":
                sheet = etree.fromstring(data)
                master, dependent = sheet.findall(".//{*}f")
                master.attrib.update({"t": "shared", "si": "0", "ref": "B2:B3"})
                dependent.attrib.update({"t": "shared", "si": "0"})
                dependent.text = None
                master.getnext().text, dependent.getnext().text = "4", "5"
                data = etree.tostring(sheet)
            if part.filename == "[Content_Types].xml":
                types = etree.fromstring(data)
                etree.SubElement(types, etree.QName(types, "Override"), PartName="/xl/calcChain.xml",
                                 ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.calcChain+xml")
                data = etree.tostring(types)
            if part.filename == "xl/_rels/workbook.xml.rels":
                rels = etree.fromstring(data)
                etree.SubElement(rels, etree.QName(rels, "Relationship"), Id="rIdChain", Target="calcChain.xml",
                                 Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/calcChain")
                data = etree.tostring(rels)
            fixture.writestr(part, data)
        fixture.writestr("xl/calcChain.xml", '<calcChain xmlns="http://schemas.openxmlformats.org/spreadsheetml/'
                         '2006/main"><c r="B2" i="2"/><c r="B3"/></calcChain>')
    return source


def test_replacing_shared_formula_master_preserves_dependent_formula(tmp_path: Path) -> None:
    # Given two formulas on the second sheet that share one definition and have cached results.
    source = _shared_workbook(tmp_path)
    before = read_tabular(source, "Data")
    assert before.rows == [["3", "4"], ["4", "5"]]

    # When a reviewer replaces the cell that defines both formulas.
    output = tmp_path / "reviewed.xlsx"
    write_tabular_changes(output, source, before, replace(before, rows=[["3", "reviewed"], before.rows[1]]))

    # Then the untouched formula still computes from its own row and retains its cached value.
    formulas = load_workbook(output)
    assert formulas["Data"]["B2"].value == "reviewed"
    assert formulas["Data"]["B3"].value == "=A3+1"
    assert formulas["Keep"]["A1"].value == "other"
    formulas.close()
    cached = load_workbook(output, data_only=True)
    assert cached["Data"]["B3"].value == 5
    cached.close()
    with ZipFile(output) as archive:
        assert "xl/calcChain.xml" not in archive.namelist()
        assert b"calcChain" not in archive.read("xl/_rels/workbook.xml.rels")
        assert b"calcChain" not in archive.read("[Content_Types].xml")


async def test_stage_five_zip_preserves_shared_formula_after_manual_review(app_client, temp_storage, tmp_path):
    # Given a completed workflow whose second worksheet contains a shared formula with a cached value.
    source = _shared_workbook(tmp_path)
    file_id = await upload_content(app_client, source.read_bytes(), "shared.xlsx", TEST_XLSX_CONTENT_TYPE)
    analysis = await app_client.post("/stage-1/analyze", json={
        "file_id": file_id, "data_model_key": "test-data-model", "external_version_number": "11.0.4",
        "sheet_name": "Data",
    })
    assert analysis.status_code == 200
    manifest = create_test_manifest_parquet(tmp_path / "manifest.parquet", [
        {"column_id": 1, "column_name": "Formula", "to_harmonize": "4", "top_harmonization": "4", "row_indices": [0]},
    ])
    store_test_completed_harmonization(temp_storage, file_id, source, manifest_path=manifest)
    saved = await app_client.post("/stage-4/overrides", headers={"If-None-Match": "*"}, json={
        "file_id": file_id, "overrides": {"1": {"col_0001": {"original_value": "4", "human_value": "reviewed"}}},
        "review_state": review_state_payload(),
    })
    assert saved.status_code == 200, saved.text

    # When the reviewer prepares the download from Stage 5.
    prepared = ReviewPackage.read(await app_client.post("/stage-5/review", json={"file_id": file_id}))

    # Then the actual ZIP contains the edited value, the dependent formula, and its cached result.
    with ZipFile(BytesIO(prepared.archive)) as archive:
        data = archive.read(next(name for name in archive.namelist() if name.endswith(".xlsx")))
    formula_book = load_workbook(BytesIO(data))
    assert formula_book["Data"]["B2"].value == "reviewed"
    assert formula_book["Data"]["B3"].value == "=A3+1"
    formula_book.close()
    cached_book = load_workbook(BytesIO(data), data_only=True)
    assert cached_book["Data"]["B3"].value == 5
    cached_book.close()


def test_editing_a_large_sheet_has_bounded_writer_memory(tmp_path: Path) -> None:
    import subprocess
    import sys

    from openpyxl import Workbook

    # Given a sheet with 120,000 text cells. A full XML tree for this fixture uses hundreds of MB.
    source = tmp_path / "large.xlsx"
    book = Workbook(write_only=True)
    sheet = book.create_sheet()
    sheet.append([f"column {i}" for i in range(10)])
    for number in range(12_000):
        sheet.append([f"row {number} col {i}" for i in range(10)])
    book.save(source)
    script = """
import resource, sys
from dataclasses import replace
from pathlib import Path
from netrias_client import read_tabular
from src.persistence.tabular_output import write_tabular_changes
source, output = map(Path, sys.argv[1:])
before = read_tabular(source)
rows = before.rows.copy()
rows[-1] = ["reviewed", *rows[-1][1:]]
after = replace(before, rows=rows)
baseline = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
write_tabular_changes(output, source, before, after)
growth = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - baseline
print(growth if sys.platform == "darwin" else growth * 1024)
"""
    output = tmp_path / "reviewed.xlsx"

    # When a fresh process edits the final row with the dataset already in memory.
    growth_bytes = int(subprocess.check_output([sys.executable, "-c", script, str(source), str(output)]))

    # Then additional peak memory stays under 96 MiB and the final edit is in the saved workbook.
    assert growth_bytes < 96 * 1024 * 1024
    assert read_tabular(output).rows[-1][0] == "reviewed"
