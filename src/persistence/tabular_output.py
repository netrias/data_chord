"""Patch changed worksheet cells while retaining native workbook content."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from itertools import chain
from pathlib import Path
from shutil import copyfile, copyfileobj
from tempfile import TemporaryDirectory
from typing import NewType
from xml.dom.minidom import Document, Element  # nosec B408: DOM node types only. defusedxml parses the input.
from zipfile import ZipFile, ZipInfo

from defusedxml.minidom import parse
from netrias_client import TabularDataset, TabularFormat, write_tabular
from openpyxl import load_workbook
from openpyxl.utils.cell import coordinate_to_tuple, get_column_letter

# SpreadsheetML uses one-based row and column numbers.
_RowNumber = NewType("_RowNumber", int)
_ColumnNumber = NewType("_ColumnNumber", int)


class _Tag(StrEnum):
    SHEET_DATA = "sheetData"
    ROW = "row"
    CELL = "c"
    VALUE = "v"
    FORMULA = "f"
    INLINE_STRING = "is"
    TEXT = "t"


_REFERENCE = "r"
_CELL_TYPE = "t"
_INLINE_STRING_TYPE = "inlineStr"


@dataclass(frozen=True)
class _CellEdit:
    column: _ColumnNumber
    value: str


def write_tabular_changes(output: Path, source: Path, before: TabularDataset, after: TabularDataset) -> None:
    if after.source_format != TabularFormat.XLSX:
        write_tabular(output, after)
        return
    if before.headers == after.headers and before.rows == after.rows:
        if source != output:
            copyfile(source, output)
        return
    sheet_path = _worksheet_part(source, after.sheet_name)
    with _atomic_workbook(output) as edited, ZipFile(source) as original:
        _copy_workbook_with_edits(original, edited, sheet_path, before, after)


@contextmanager
def _atomic_workbook(output: Path) -> Iterator[ZipFile]:
    """Publish only after the new ZIP and the original workbook have both closed."""
    with TemporaryDirectory(dir=output.parent) as directory:
        temporary = Path(directory) / "output.xlsx"
        with ZipFile(temporary, "w") as archive:
            yield archive
        temporary.replace(output)


def _copy_workbook_with_edits(
    source: ZipFile, output: ZipFile, sheet_path: str, before: TabularDataset, after: TabularDataset,
) -> None:
    for part in source.infolist():
        if part.filename == sheet_path:
            _write_edited_sheet(source, output, part, before, after)
            continue
        with source.open(part) as reader, output.open(part, "w") as writer:
            copyfileobj(reader, writer)


def _write_edited_sheet(
    source: ZipFile, output: ZipFile, part: ZipInfo, before: TabularDataset, after: TabularDataset,
) -> None:
    with source.open(part) as reader:
        document = parse(reader)
    _patch_rows(document, before, after)
    output.writestr(part, document.toxml(encoding="UTF-8"))


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


def _elements(parent: Element, local_name: _Tag) -> Iterator[Element]:
    return (node for node in parent.childNodes if isinstance(node, Element) and node.localName == local_name)


def _append_element(parent: Element, name: _Tag) -> Element:
    qualified = f"{parent.prefix}:{name}" if parent.prefix else name
    assert parent.ownerDocument is not None
    child = parent.ownerDocument.createElementNS(parent.namespaceURI, qualified)
    parent.appendChild(child)
    return child


def _indexed_element[Position: int](
    parent: Element, elements: dict[Position, Element], position: Position, tag: _Tag, reference: str,
) -> Element:
    existing = elements.get(position)
    if existing is not None:
        return existing
    element = _append_element(parent, tag)
    element.setAttribute(_REFERENCE, reference)
    following = next((child for index, child in elements.items() if index > position), None)
    if following is not None:
        parent.insertBefore(element, following)
    return element


def _patch_rows(document: Document, before: TabularDataset, after: TabularDataset) -> None:
    sheet_data = document.getElementsByTagNameNS("*", _Tag.SHEET_DATA)[0]
    native_rows: dict[_RowNumber, Element] = {}
    position = 0
    for row in _elements(sheet_data, _Tag.ROW):
        position = int(row.getAttribute(_REFERENCE) or position + 1)
        native_rows[_RowNumber(position)] = row
    old_rows = chain([before.headers], before.rows)
    new_rows = chain([after.headers], after.rows)
    for number, (old, new) in enumerate(zip(old_rows, new_rows, strict=True), 1):
        if old == new:
            continue
        row_number = _RowNumber(number)
        row = _indexed_element(sheet_data, native_rows, row_number, _Tag.ROW, str(row_number))
        _patch_cells(row, row_number, _cell_edits(old, new))


def _cell_edits(old: list[str], new: list[str]) -> Iterator[_CellEdit]:
    for column, (previous, value) in enumerate(zip(old, new, strict=True), 1):
        if previous != value:
            yield _CellEdit(_ColumnNumber(column), value)


def _cells_by_column(row: Element) -> dict[_ColumnNumber, Element]:
    cells: dict[_ColumnNumber, Element] = {}
    position = 0
    for cell in _elements(row, _Tag.CELL):
        address = cell.getAttribute(_REFERENCE)
        if address:
            _, position = coordinate_to_tuple(address)
        else:
            position += 1
        cells[_ColumnNumber(position)] = cell
    return cells


def _patch_cells(row: Element, number: _RowNumber, edits: Iterator[_CellEdit]) -> None:
    cells = _cells_by_column(row)
    for edit in edits:
        address = f"{get_column_letter(edit.column)}{number}"
        cell = _indexed_element(row, cells, edit.column, _Tag.CELL, address)
        _write_inline_text(cell, edit.value)


def _write_inline_text(cell: Element, value: str) -> None:
    for child in list(cell.childNodes):
        if isinstance(child, Element) and child.localName in {_Tag.VALUE, _Tag.INLINE_STRING, _Tag.FORMULA}:
            cell.removeChild(child)
    cell.setAttribute(_CELL_TYPE, _INLINE_STRING_TYPE)
    inline_string = _append_element(cell, _Tag.INLINE_STRING)
    text = _append_element(inline_string, _Tag.TEXT)
    text.setAttribute("xml:space", "preserve")
    assert cell.ownerDocument is not None
    text.appendChild(cell.ownerDocument.createTextNode(value))
