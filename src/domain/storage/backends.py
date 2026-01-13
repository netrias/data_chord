"""
Abstract storage interface and local filesystem implementation.

Enables swapping backends (local, S3) without changing caller code.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

from .file_types import FileType, build_file_name

logger = logging.getLogger(__name__)


class StorageBackend(ABC):
    """Abstract interface for raw byte storage."""

    @abstractmethod
    def write(self, file_id: str, file_type: FileType, data: bytes) -> None:
        """Write raw bytes to storage."""

    @abstractmethod
    def read(self, file_id: str, file_type: FileType) -> bytes | None:
        """Read raw bytes from storage. Returns None if not found."""

    @abstractmethod
    def delete(self, file_id: str, file_type: FileType) -> None:
        """Delete a file from storage."""

    @abstractmethod
    def exists(self, file_id: str, file_type: FileType) -> bool:
        """Check if a file exists."""


class LocalStorageBackend(StorageBackend):
    """Local filesystem implementation."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, file_id: str, file_type: FileType) -> Path:
        """Construct path with traversal protection."""
        name = build_file_name(file_id, file_type)
        path = (self._base_dir / name).resolve()
        if not path.is_relative_to(self._base_dir.resolve()):
            raise ValueError(f"Path traversal attempt detected: {file_id}")
        return path

    def write(self, file_id: str, file_type: FileType, data: bytes) -> None:
        """Write raw bytes to local file."""
        path = self._path_for(file_id, file_type)
        path.write_bytes(data)
        logger.info(
            "Wrote file",
            extra={"file_id": file_id, "file_type": file_type.name, "path": str(path)},
        )

    def read(self, file_id: str, file_type: FileType) -> bytes | None:
        """Read raw bytes from local file."""
        path = self._path_for(file_id, file_type)
        if not path.exists():
            return None
        return path.read_bytes()

    def delete(self, file_id: str, file_type: FileType) -> None:
        """Delete local file."""
        path = self._path_for(file_id, file_type)
        path.unlink(missing_ok=True)
        logger.info(
            "Deleted file",
            extra={"file_id": file_id, "file_type": file_type.name, "path": str(path)},
        )

    def exists(self, file_id: str, file_type: FileType) -> bool:
        """Check if local file exists."""
        return self._path_for(file_id, file_type).exists()


__all__ = [
    "StorageBackend",
    "LocalStorageBackend",
]
