"""Semantic names for the JSON artifacts persisted outside the upload tree."""

from __future__ import annotations

from enum import Enum


class FileType(Enum):
    """The durable sidecar artifacts FileStore owns.

    Uploads, harmonized CSVs, and SDK parquet manifests are managed by
    UploadStorage or external services. FileStore only owns app-authored JSON
    records that need to survive server restarts.
    """

    COLUMN_MAPPING = "mapping"
    REVIEW_OVERRIDES = "overrides"
    PV_MANIFEST = "pv_manifest"

    @property
    def suffix(self) -> str:
        """Type identifier used in file name."""
        return self.value

    @property
    def extension(self) -> str:
        """File extension for every FileStore artifact."""
        return "json"


FILE_NAME_TEMPLATE = "{file_id}_{suffix}.{extension}"


def build_file_name(file_id: str, file_type: FileType) -> str:
    """Construct a file name from components."""
    return FILE_NAME_TEMPLATE.format(
        file_id=file_id,
        suffix=file_type.suffix,
        extension=file_type.extension,
    )


__all__ = [
    "FileType",
    "FILE_NAME_TEMPLATE",
    "build_file_name",
]
