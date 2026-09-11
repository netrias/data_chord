"""The review baseline preserves source and provider text exactly."""

from dataclasses import asdict

from hypothesis import example, given
from hypothesis import strategies as st

from src.domain.harmonization import MatchFidelity
from src.domain.manifest import ManifestRow


@given(original=st.text(), recommendation=st.text())
@example(original=" Foo ", recommendation="")
@example(original=" Foo ", recommendation=" \t\n")
@example(original=" Foo ", recommendation=" Bar ")
def test_baseline_selects_recommendation_or_original_without_changing_text(
    original: str,
    recommendation: str,
) -> None:
    # Given: a provider result and original text, including significant whitespace.
    row = ManifestRow(
        job_id="job",
        column_id=0,
        column_name="diagnosis",
        to_harmonize=original,
        top_harmonization=recommendation,
        ontology_id=None,
        top_harmonizations=[],
        match_fidelity=MatchFidelity.STRONG,
        error=None,
        row_indices=[0],
    )
    stored_fields = asdict(row)

    # When: review reads the value before any manual override.
    baseline = row.baseline_value

    # Then: blank recommendations keep the source; other results keep their exact text.
    if not recommendation.strip():
        assert baseline == original
    else:
        assert baseline == recommendation
    assert asdict(row) == stored_fields
