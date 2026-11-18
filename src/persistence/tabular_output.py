"""Patch changed worksheet cells while retaining native workbook content."""

from collections.abc import Iterator
from itertools import chain
from pathlib import Path
from shutil import copyfile, copyfileobj
from tempfile import TemporaryDirectory
from xml.dom.minidom import Document, Element  # nosec B408: DOM node types only. defusedxml parses the input.
from zipfile import ZipFile

from defusedxml.minidom import parse
from netrias_client import TabularDataset, TabularFormat, write_tabular
from openpyxl import load_workbook
from openpyxl.utils.cell import coordinate_to_tuple, get_column_letter


def write_tabular_changes(output: Path, source: Path, before: TabularDataset, after: TabularDataset) -> None:
    if after.source_format != TabularFormat.XLSX:
        write_tabular(output, after)
        return
    if before.headers == after.headers and before.rows == after.rows:
        if source != output:
            copyfile(source, output)
        return
    sheet_path = _worksheet_part(source, after.sheet_name)
    with TemporaryDirectory(dir=output.parent) as directory:
        temporary = Path(directory) / "output.xlsx"
        with ZipFile(source) as original, ZipFile(temporary, "w") as edited:
            for member in original.infolist():
                if member.filename == sheet_path:
                    with original.open(member) as reader:
                        document = parse(reader)
                    _patch_rows(document, before, after)
                    edited.writestr(member, document.toxml(encoding="UTF-8"))
                else:
                    with original.open(member) as reader, edited.open(member, "w") as writer:
                        copyfileobj(reader, writer)
        # Copy back only after the package has been completely written.
        temporary.replace(output)


def _worksheet_part(source: Path, sheet_name: str | None) -> str:
    workbook = load_workbook(source, read_only=True)
    try:
        sheet = workbook[sheet_name] if sheet_name else workbook.worksheets[0]
        part = getattr(sheet, "_worksheet_path", None)
        if not isinstance(part, str):
            raise ValueError("The selected workbook sheet contains no dataset.")
        return part
    finally:
        workbook.close()


def _elements(parent: Element, local_name: str) -> Iterator[Element]:
    return (node for node in parent.childNodes if isinstance(node, Element) and node.localName == local_name)


def _append_element(parent: Element, name: str) -> Element:
    qualified = f"{parent.prefix}:{name}" if parent.prefix else name
    assert parent.ownerDocument is not None
    child = parent.ownerDocument.createElementNS(parent.namespaceURI, qualified)
    parent.appendChild(child)
    return child


def _patch_rows(document: Document, before: TabularDataset, after: TabularDataset) -> None:
    sheet_data = document.getElementsByTagNameNS("*", "sheetData")[0]
    native_rows: dict[int, Element] = {}
    position = 0
    for row in _elements(sheet_data, "row"):
        position = int(row.getAttribute("r") or position + 1)
        native_rows[position] = row
    old_rows = chain([before.headers], before.rows)
    new_rows = chain([after.headers], after.rows)
    for number, (old, new) in enumerate(zip(old_rows, new_rows, strict=True), 1):
        if old == new:
            continue
        row = native_rows.get(number)
        if row is None:
            row = _append_element(sheet_data, "row")
            row.setAttribute("r", str(number))
            following = next((r for n, r in native_rows.items() if n > number), None)
            if following is not None:
                sheet_data.insertBefore(row, following)
        _patch_cells(row, number, old, new)


def _patch_cells(row: Element, number: int, old: list[str], new: list[str]) -> None:
    cells: dict[int, Element] = {}
    position = 0
    for cell in _elements(row, "c"):
        address = cell.getAttribute("r")
        position = coordinate_to_tuple(address)[1] if address else position + 1
        cells[position] = cell
    for column, (previous, value) in enumerate(zip(old, new, strict=True), 1):
        if previous == value:
            continue
        cell = cells.get(column)
        if cell is None:
            cell = _append_element(row, "c")
            cell.setAttribute("r", f"{get_column_letter(column)}{number}")
            following = next((c for n, c in cells.items() if n > column), None)
            if following is not None:
                row.insertBefore(cell, following)
        for child in list(cell.childNodes):
            if isinstance(child, Element) and child.localName in {"v", "is", "f"}:
                cell.removeChild(child)
        cell.setAttribute("t", "inlineStr")
        text = _append_element(_append_element(cell, "is"), "t")
        text.setAttribute("xml:space", "preserve")
        assert cell.ownerDocument is not None
        text.appendChild(cell.ownerDocument.createTextNode(value))
