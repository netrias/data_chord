"""Write dataset edits while preserving untouched native Excel cells and workbook parts."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from shutil import copyfile, copyfileobj
from tempfile import TemporaryDirectory
from typing import IO, NewType
from zipfile import ZipFile

from lxml import etree
from netrias_client import TabularDataset, TabularFormat, write_tabular
from openpyxl import load_workbook
from openpyxl.formula.translate import Translator
from openpyxl.utils.cell import coordinate_to_tuple, get_column_letter

_RowNumber = NewType("_RowNumber", int)
_ColumnNumber = NewType("_ColumnNumber", int)
_REFERENCE = "r"
_TYPE = "t"
_SHARED_FORMULA = "shared"
_SHARED_INDEX = "si"
_CONTENT_TYPES = "[Content_Types].xml"
_CALC_CHAIN_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.calcChain+xml"


class _Tag(StrEnum):
    SHEET_DATA = "sheetData"
    ROW = "row"
    CELL = "c"
    VALUE = "v"
    FORMULA = "f"
    INLINE_STRING = "is"
    TEXT = "t"


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
        _copy_workbook(original, edited, sheet_path, _SheetEdits(before, after))


@contextmanager
def _atomic_workbook(output: Path) -> Iterator[ZipFile]:
    with TemporaryDirectory(dir=output.parent) as directory:
        temporary = Path(directory) / "output.xlsx"
        with ZipFile(temporary, "w") as archive:
            yield archive
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


def _xml(content: bytes) -> etree._Element:
    return etree.fromstring(content, parser=etree.XMLParser(resolve_entities=False, no_network=True))


def _copy_workbook(source: ZipFile, output: ZipFile, sheet_path: str, edits: "_SheetEdits") -> None:
    # Excel rebuilds its optional calculation chain. A stale chain can refer to a replaced formula.
    types = _xml(source.read(_CONTENT_TYPES))
    chains = {node.attrib["PartName"].lstrip("/") for node in types if node.get("ContentType") == _CALC_CHAIN_TYPE}
    for part in source.infolist():
        if part.filename in chains:
            continue
        with source.open(part) as reader, output.open(part, "w") as writer:
            if part.filename == sheet_path:
                _stream_worksheet(reader, writer, edits)
            elif chains and (part.filename == _CONTENT_TYPES or part.filename.endswith(".rels")):
                _write_without_calc_chain(reader, writer)
            else:
                copyfileobj(reader, writer)


def _write_without_calc_chain(reader: IO[bytes], writer: IO[bytes]) -> None:
    root = _xml(reader.read())
    for child in list(root):
        if child.get("ContentType") == _CALC_CHAIN_TYPE or child.get("Type", "").endswith("/calcChain"):
            root.remove(child)
    writer.write(etree.tostring(root, encoding="utf-8"))


def _stream_worksheet(reader: IO[bytes], writer: IO[bytes], edits: "_SheetEdits") -> None:
    events = iter(etree.iterparse(reader, events=("start", "end", "comment", "pi"), resolve_entities=False,
                                  load_dtd=False, no_network=True))
    _, root = next((event, node) for event, node in events if event == "start")
    if root.getroottree().docinfo.doctype:
        raise ValueError("Worksheet XML must not contain a document type declaration.")
    sheet_data_tag = etree.QName(root, _Tag.SHEET_DATA)
    with etree.xmlfile(writer, encoding="utf-8") as xml, xml.element(root.tag, root.attrib, nsmap=root.nsmap):
        for event, child in events:
            if child.getparent() is not root:
                continue
            if event == "start" and child.tag == sheet_data_tag:
                _stream_rows(events, child, xml, edits)
            elif event != "start":
                xml.write(child)
            else:
                continue
            root.remove(child)


def _stream_rows(events, sheet, xml, edits: "_SheetEdits") -> None:
    last = 0
    row_tag = etree.QName(sheet, _Tag.ROW)
    with xml.element(sheet.tag, sheet.attrib, nsmap=sheet.nsmap):
        for event, row in events:
            if event == "end" and row is sheet:
                break
            if event == "start" or row.getparent() is not sheet:
                continue
            if row.tag == row_tag:
                position = int(row.get(_REFERENCE, last + 1))
                edits.write_missing(xml, row_tag, last + 1, position)
                edits.apply(row, _RowNumber(position))
                last = position
            xml.write(row)
            sheet.remove(row)
        edits.write_missing(xml, row_tag, last + 1, len(edits.after.rows) + 2)


@dataclass
class _SheetEdits:
    before: TabularDataset
    after: TabularDataset
    replaced_masters: dict[str, Translator] = field(default_factory=dict)

    def changes(self, number: int) -> dict[_ColumnNumber, str]:
        if number > len(self.after.rows) + 1:
            return {}
        old = self.before.headers if number == 1 else self.before.rows[number - 2]
        new = self.after.headers if number == 1 else self.after.rows[number - 2]
        if old == new:
            return {}
        return {
            _ColumnNumber(index): value for index, (previous, value) in enumerate(zip(old, new, strict=True), 1)
            if previous != value
        }

    def write_missing(self, xml, tag: etree.QName, start: int, stop: int) -> None:
        for number in range(start, min(stop, len(self.after.rows) + 2)):
            if self.changes(number):
                row = etree.Element(tag, {_REFERENCE: str(number)})
                self.apply(row, _RowNumber(number))
                xml.write(row)

    def apply(self, row: etree._Element, number: _RowNumber) -> None:
        changes = self.changes(number)
        if not changes and not self.replaced_masters:
            return
        column = 0
        for cell in row.iterchildren(etree.QName(row, _Tag.CELL)):
            ref = cell.get(_REFERENCE)
            column = coordinate_to_tuple(ref)[1] if ref else column + 1
            address = ref or f"{get_column_letter(column)}{number}"
            value = changes.pop(_ColumnNumber(column), None)
            self._preserve_formula(cell, address, value is not None)
            if value is not None:
                _write_text(cell, value)
        _insert_cells(row, number, changes)

    def _preserve_formula(self, cell: etree._Element, address: str, replacing: bool) -> None:
        formula = cell.find(etree.QName(cell, _Tag.FORMULA))
        if formula is None or formula.get(_TYPE) != _SHARED_FORMULA:
            return
        group = formula.attrib[_SHARED_INDEX]
        if formula.text:
            self.replaced_masters.pop(group, None)
            if replacing:
                self.replaced_masters[group] = Translator("=" + formula.text, origin=address)
        elif group in self.replaced_masters and not replacing:
            formula.text = self.replaced_masters[group].translate_formula(address)[1:]
            del formula.attrib[_SHARED_INDEX], formula.attrib[_TYPE]


def _insert_cells(row: etree._Element, number: _RowNumber, changes: dict[_ColumnNumber, str]) -> None:
    if not changes:
        return
    # Missing cells come from padded dataset rows. Insert them before the next native column.
    existing = iter(row.iterchildren(etree.QName(row, _Tag.CELL)))
    following = next(existing, None)
    position = 0
    for column, value in changes.items():
        while following is not None:
            ref = following.get(_REFERENCE)
            index = coordinate_to_tuple(ref)[1] if ref else position + 1
            if index > column:
                break
            position, following = index, next(existing, None)
        cell = etree.Element(etree.QName(row, _Tag.CELL), {_REFERENCE: f"{get_column_letter(column)}{number}"})
        if following is not None:
            following.addprevious(cell)
        else:
            row.append(cell)
        _write_text(cell, value)


def _write_text(cell: etree._Element, value: str) -> None:
    for tag in (_Tag.FORMULA, _Tag.VALUE, _Tag.INLINE_STRING):
        child = cell.find(etree.QName(cell, tag))
        if child is not None:
            cell.remove(child)
    cell.set(_TYPE, "inlineStr")
    inline = etree.Element(etree.QName(cell, _Tag.INLINE_STRING))
    cell.insert(0, inline)
    text = etree.SubElement(inline, etree.QName(cell, _Tag.TEXT))
    text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    text.text = value
