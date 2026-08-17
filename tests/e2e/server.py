"""AWS-free application wiring for browser behavior tests."""

from pathlib import Path

from src.app import dependencies
from src.integrations.reference_data_file import FileReferenceDataRepository

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
dependencies._reference_data_repository = FileReferenceDataRepository(  # noqa: SLF001
    _REPOSITORY_ROOT / "tests" / "e2e" / "fixtures" / "reference-data.synthetic.json"
)

from backend.app.main import app  # noqa: E402

__all__ = ["app"]
