"""
Format-specific serializers for converting artifacts to/from disk storage.

Decouples file format (JSON, Parquet, CSV) from storage backend logic.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from .file_types import FileType


class Serializer(ABC):
    """Abstract serializer interface."""

    @abstractmethod
    def serialize(self, data: Any) -> bytes:
        """Encode data to bytes."""

    @abstractmethod
    def deserialize(self, raw: bytes) -> Any:
        """Decode bytes to data."""


class JSONSerializer(Serializer):
    """JSON serialization for dict/list data."""

    def serialize(self, data: Any) -> bytes:
        return json.dumps(data, indent=2, default=str).encode("utf-8")

    def deserialize(self, raw: bytes) -> Any:
        return json.loads(raw.decode("utf-8"))


class ParquetSerializer(Serializer):
    """Parquet serialization - passes through bytes.

    Parquet files are read/written as bytes. Caller handles
    conversion to/from DataFrame or other structures.
    """

    def serialize(self, data: bytes) -> bytes:
        return data

    def deserialize(self, raw: bytes) -> bytes:
        return raw


class RawBytesSerializer(Serializer):
    """Pass-through for raw file content (CSV, etc.)."""

    def serialize(self, data: bytes) -> bytes:
        return data

    def deserialize(self, raw: bytes) -> bytes:
        return raw


_SERIALIZERS: dict[FileType, Serializer] = {
    FileType.UPLOAD_META: JSONSerializer(),
    FileType.COLUMN_MAPPING: JSONSerializer(),
    FileType.REVIEW_OVERRIDES: JSONSerializer(),
    FileType.HARMONIZATION_MANIFEST: ParquetSerializer(),
    FileType.ORIGINAL_CSV: RawBytesSerializer(),
    FileType.HARMONIZED_CSV: RawBytesSerializer(),
}


def get_serializer(file_type: FileType) -> Serializer:
    """Get the appropriate serializer for a file type."""
    return _SERIALIZERS[file_type]


__all__ = [
    "Serializer",
    "get_serializer",
]
