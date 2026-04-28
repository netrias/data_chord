"""FileStore facade with typed artifact accessors."""

from __future__ import annotations

from src.domain.pv_manifest import PVManifest
from src.domain.review_overrides import ReviewOverrides

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

    def save(self, file_id: str, file_type: FileType, data: object) -> None:
        """Save data with automatic serialization."""
        serializer = get_serializer(file_type)
        raw = serializer.serialize(data)
        self._backend.write(file_id, file_type, raw)

    def load(self, file_id: str, file_type: FileType) -> object | None:
        """Load data with automatic deserialization."""
        raw = self._backend.read(file_id, file_type)
        if raw is None:
            return None
        serializer = get_serializer(file_type)
        return serializer.deserialize(raw)

    def save_review_overrides(self, overrides: ReviewOverrides) -> None:
        self.save(overrides.file_id, FileType.REVIEW_OVERRIDES, overrides.to_store())

    def load_review_overrides(self, file_id: str) -> ReviewOverrides | None:
        return ReviewOverrides.from_store(self.load(file_id, FileType.REVIEW_OVERRIDES), file_id)

    def save_pv_manifest(self, file_id: str, manifest: PVManifest) -> None:
        self.save(file_id, FileType.PV_MANIFEST, manifest.to_store())

    def load_pv_manifest(self, file_id: str) -> PVManifest | None:
        return PVManifest.from_store(self.load(file_id, FileType.PV_MANIFEST))

    def delete(self, file_id: str, file_type: FileType) -> None:
        """Delete a file."""
        self._backend.delete(file_id, file_type)

    def exists(self, file_id: str, file_type: FileType) -> bool:
        """Check if a file exists."""
        return self._backend.exists(file_id, file_type)


__all__ = ["FileStore"]
