"""
Semantic file types and naming conventions for stage-specific artifacts.

Centralizes file naming logic and enumerates valid artifact types.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FileType(Enum):
    """Semantic file types used across stages."""

    # Stage 1 - Upload
    UPLOAD_META = ("meta", "json")

    # Stage 2 - Mapping
    COLUMN_MAPPING = ("mapping", "json")

    # Stage 3 - Harmonization
    HARMONIZATION_MANIFEST = ("harmonization", "parquet")

    # Stage 4 - Review
    REVIEW_OVERRIDES = ("overrides", "json")

    # PV Manifest - persists column-to-CDE mappings and PV sets
    PV_MANIFEST = ("pv_manifest", "json")

    # Data files
    ORIGINAL_CSV = ("original", "csv")
    HARMONIZED_CSV = ("harmonized", "csv")

    @property
    def suffix(self) -> str:
        """Type identifier used in file name."""
        return self.value[0]

    @property
    def extension(self) -> str:
        """File extension."""
        return self.value[1]


FILE_NAME_TEMPLATE = "{file_id}_{suffix}.{extension}"

_SUFFIX_TO_FILE_TYPE: dict[str, FileType] = {ft.suffix: ft for ft in FileType}


@dataclass(frozen=True)
class ParsedFileName:
    """Properties extracted from a storage file name."""

    file_id: str
    file_type: FileType
    raw_name: str


def build_file_name(file_id: str, file_type: FileType) -> str:
    """Construct a file name from components."""
    return FILE_NAME_TEMPLATE.format(
        file_id=file_id,
        suffix=file_type.suffix,
        extension=file_type.extension,
    )


def parse_file_name(name: str) -> ParsedFileName | None:
    """Extract components from a file name. Returns None if unparseable."""
    if "." not in name or "_" not in name:
        return None

    base, extension = name.rsplit(".", 1)
    parts = base.rsplit("_", 1)
    if len(parts) != 2:
        return None

    file_id, suffix = parts
    file_type = _SUFFIX_TO_FILE_TYPE.get(suffix)
    if file_type is None or file_type.extension != extension:
        return None

    return ParsedFileName(file_id=file_id, file_type=file_type, raw_name=name)


__all__ = [
    "FileType",
    "FILE_NAME_TEMPLATE",
    "ParsedFileName",
    "build_file_name",
    "parse_file_name",
]
