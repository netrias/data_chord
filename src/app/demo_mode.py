"""Prepare and expose the one packaged demo workflow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.app import dependencies
from src.auth.user_context import LOCAL_USER_ID
from src.domain.dataset_workflow_ids import (
    DatasetWorkflowId,
    dataset_workflow_id_from_string,
)
from src.integrations.reference_data_file import load_reference_models
from src.integrations.sqlite_reference_data import SqliteReferenceDataImporter
from src.paths import PROJECT_ROOT
from src.persistence.workflow_artifacts import load_upload_artifact, save_upload_artifacts
from src.settings import get_reference_database_path
from src.storage import UploadedFileMeta, UserContext

DEMO_WORKFLOW_ID: DatasetWorkflowId = dataset_workflow_id_from_string(
    "00000000000000000000000000000001"
)
DEMO_SAMPLE_PATH = PROJECT_ROOT / "demo" / "sample.csv"
DEMO_REFERENCE_PATH = PROJECT_ROOT / "demo" / "reference-data.synthetic.json"


@dataclass
class _PackagedUpload:
    path: Path
    filename: str | None = "sample.csv"
    content_type: str | None = "text/csv"
    _position: int = 0

    async def read(self, size: int = -1) -> bytes:
        content = self.path.read_bytes()
        if size < 0:
            chunk = content[self._position :]
            self._position = len(content)
            return chunk
        chunk = content[self._position : self._position + size]
        self._position += len(chunk)
        return chunk

    async def close(self) -> None:
        return None


async def prepare_demo_runtime() -> None:
    """Load the demo standard and sample through the normal local boundaries."""
    models = load_reference_models(DEMO_REFERENCE_PATH)
    SqliteReferenceDataImporter(get_reference_database_path()).import_models(models)

    user = UserContext(user_id=LOCAL_USER_ID)
    upload_storage = dependencies.get_upload_storage()
    workflow_storage = dependencies.get_workflow_storage()
    meta = await upload_storage.store(_PackagedUpload(DEMO_SAMPLE_PATH), DEMO_WORKFLOW_ID)
    workflow_storage.create_workflow(user, DEMO_WORKFLOW_ID)
    save_upload_artifacts(workflow_storage, user, upload_storage, meta)


def get_demo_upload() -> UploadedFileMeta:
    """Read the prepared sample through the normal upload artifact boundary."""
    meta = load_upload_artifact(
        dependencies.get_upload_storage(),
        dependencies.get_workflow_storage(),
        dependencies.get_user_context(),
        DEMO_WORKFLOW_ID,
    )
    if meta is None:
        raise RuntimeError("Demo sample is not prepared")
    return meta


__all__ = [
    "DEMO_REFERENCE_PATH",
    "DEMO_SAMPLE_PATH",
    "DEMO_WORKFLOW_ID",
    "get_demo_upload",
    "prepare_demo_runtime",
]
