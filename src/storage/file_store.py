"""Local JSON store for durable app-authored artifacts."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.domain.pv_manifest import PVManifest
from src.domain.review_overrides import ReviewOverrides
from src.domain.workflow_state import WorkflowState

from .file_types import FileType, build_file_name

logger = logging.getLogger(__name__)


class FileStore:
    """Persist small JSON artifacts by file id and semantic artifact type.

    The app currently needs one local store for column mapping artifacts, review
    overrides, and PV manifests. Keeping JSON persistence here avoids extra
    backend and serializer objects that were not used anywhere else.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, file_id: str, file_type: FileType) -> Path:
        name = build_file_name(file_id, file_type)
        path = (self._base_dir / name).resolve()
        if not path.is_relative_to(self._base_dir.resolve()):
            raise ValueError(f"Path traversal attempt detected: {file_id}")
        return path

    def save(self, file_id: str, file_type: FileType, data: object) -> None:
        """Save a JSON-serializable artifact."""
        path = self._path_for(file_id, file_type)
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        logger.info(
            "Wrote file store artifact",
            extra={"file_id": file_id, "file_type": file_type.name, "path": str(path)},
        )

    def load(self, file_id: str, file_type: FileType) -> object | None:
        """Load a JSON artifact. Returns None when the artifact does not exist."""
        path = self._path_for(file_id, file_type)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save_column_mapping(self, file_id: str, document: object) -> None:
        self.save(file_id, FileType.COLUMN_MAPPING, document)

    def load_column_mapping(self, file_id: str) -> object | None:
        return self.load(file_id, FileType.COLUMN_MAPPING)

    def delete_column_mapping(self, file_id: str) -> bool:
        existed = self.exists(file_id, FileType.COLUMN_MAPPING)
        self.delete(file_id, FileType.COLUMN_MAPPING)
        return existed

    def save_review_overrides(self, overrides: ReviewOverrides) -> None:
        self.save(overrides.file_id, FileType.REVIEW_OVERRIDES, overrides.to_store())

    def load_review_overrides(self, file_id: str) -> ReviewOverrides | None:
        return ReviewOverrides.from_store(self.load(file_id, FileType.REVIEW_OVERRIDES), file_id)

    def delete_review_overrides(self, file_id: str) -> bool:
        existed = self.exists(file_id, FileType.REVIEW_OVERRIDES)
        self.delete(file_id, FileType.REVIEW_OVERRIDES)
        return existed

    def save_pv_manifest(self, file_id: str, manifest: PVManifest) -> None:
        self.save(file_id, FileType.PV_MANIFEST, manifest.to_store())

    def load_pv_manifest(self, file_id: str) -> PVManifest | None:
        return PVManifest.from_store(self.load(file_id, FileType.PV_MANIFEST))

    def save_workflow_state(self, state: WorkflowState) -> None:
        self.save(state.file_id, FileType.WORKFLOW_STATE, state.to_store())

    def load_workflow_state(self, file_id: str) -> WorkflowState | None:
        return WorkflowState.from_store(self.load(file_id, FileType.WORKFLOW_STATE), file_id)

    def delete(self, file_id: str, file_type: FileType) -> None:
        """Delete a file."""
        path = self._path_for(file_id, file_type)
        path.unlink(missing_ok=True)
        logger.info(
            "Deleted file store artifact",
            extra={"file_id": file_id, "file_type": file_type.name, "path": str(path)},
        )

    def exists(self, file_id: str, file_type: FileType) -> bool:
        """Check if a file exists."""
        return self._path_for(file_id, file_type).exists()


__all__ = ["FileStore"]
