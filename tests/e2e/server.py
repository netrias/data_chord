"""AWS-free application wiring for browser behavior tests."""

from pathlib import Path

from src.app import dependencies
from src.integrations.demo_harmonization_cache import DemoHarmonizationCache
from src.integrations.reference_data_file import FileReferenceDataRepository
from src.integrations.value_overlap_cde_recommendation import ValueOverlapCdeRecommender

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
dependencies._reference_data_repository = FileReferenceDataRepository(  # noqa: SLF001
    _REPOSITORY_ROOT / "tests" / "e2e" / "fixtures" / "reference-data.synthetic.json"
)
dependencies._cde_recommender = ValueOverlapCdeRecommender()  # noqa: SLF001
dependencies._harmonization_cache = DemoHarmonizationCache()  # noqa: SLF001

from backend.app.main import app  # noqa: E402

__all__ = ["app"]
