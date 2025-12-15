"""FileStore facade - unified interface for typed file storage."""

from __future__ import annotations

from typing import Any

from .backends import StorageBackend
from .file_types import FileType
from .serializers import get_serializer


class FileStore:
    """Unified interface for typed file storage.

    Callers only need to specify file_id and FileType.
    Serialization is handled automatically based on file type.
    """

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    def save(self, file_id: str, file_type: FileType, data: Any) -> None:
        """Save data with automatic serialization."""
        serializer = get_serializer(file_type)
        raw = serializer.serialize(data)
        self._backend.write(file_id, file_type, raw)

    def load(self, file_id: str, file_type: FileType) -> Any | None:
        """Load data with automatic deserialization."""
        raw = self._backend.read(file_id, file_type)
        if raw is None:
            return None
        serializer = get_serializer(file_type)
        return serializer.deserialize(raw)

    def delete(self, file_id: str, file_type: FileType) -> None:
        """Delete a file."""
        self._backend.delete(file_id, file_type)

    def exists(self, file_id: str, file_type: FileType) -> bool:
        """Check if a file exists."""
        return self._backend.exists(file_id, file_type)


__all__ = ["FileStore"]
