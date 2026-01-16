"""Unit tests for PV override protection.

Validates that valid original values are never replaced by AI harmonization,
even when the AI suggestion is also a valid PV.
"""

from __future__ import annotations

from src.domain.pv_validation import (
    AdjustmentSource,
    compute_pv_adjustment,
)


class TestPVOverrideProtection:
    """Original values in PV set should be preserved, not replaced by AI."""

    def test_original_valid_ai_different_returns_pv_override(self) -> None:
        """When original is valid but AI suggests different value, revert to original."""
        # Given: Original "lung cancer" is valid, AI suggests "Lung Cancer"
        pv_set = frozenset(["lung cancer", "Lung Cancer", "breast cancer"])

        # When: Computing adjustment
        result = compute_pv_adjustment(
            original_value="lung cancer",
            top_harmonization="Lung Cancer",
            top_suggestions=["Lung Cancer", "LUNG CANCER"],
            pv_set=pv_set,
        )

        # Then: Reverts to original with PV_OVERRIDE source
        assert result.is_conformant is True
        assert result.adjusted_value == "lung cancer"
        assert result.adjustment_source == AdjustmentSource.PV_OVERRIDE
        assert result.attempted_value == "Lung Cancer"

    def test_original_valid_ai_same_no_adjustment(self) -> None:
        """When original equals AI suggestion, no adjustment needed."""
        # Given: Original and AI both suggest "Lung Cancer"
        pv_set = frozenset(["Lung Cancer", "breast cancer"])

        # When: Computing adjustment
        result = compute_pv_adjustment(
            original_value="Lung Cancer",
            top_harmonization="Lung Cancer",
            top_suggestions=["Lung Cancer"],
            pv_set=pv_set,
        )

        # Then: Conformant with no adjustment
        assert result.is_conformant is True
        assert result.adjusted_value is None
        assert result.adjustment_source is None

    def test_original_invalid_ai_valid_uses_ai(self) -> None:
        """When original is invalid but AI is valid, use AI suggestion."""
        # Given: Original "lung canser" (typo) is invalid, AI suggests valid value
        pv_set = frozenset(["Lung Cancer", "breast cancer"])

        # When: Computing adjustment
        result = compute_pv_adjustment(
            original_value="lung canser",
            top_harmonization="Lung Cancer",
            top_suggestions=["Lung Cancer"],
            pv_set=pv_set,
        )

        # Then: Uses AI suggestion (no adjustment record since top_harmonization is used)
        assert result.is_conformant is True
        assert result.adjusted_value is None
        assert result.adjustment_source is None

    def test_original_invalid_ai_invalid_alt_valid_uses_alt(self) -> None:
        """When original and AI are invalid but alternative is valid, use alternative."""
        # Given: Original and AI are invalid, but alternative "Lung Cancer" is valid
        pv_set = frozenset(["Lung Cancer", "breast cancer"])

        # When: Computing adjustment
        result = compute_pv_adjustment(
            original_value="lung canser",
            top_harmonization="LUNG CANCER",  # Invalid (case matters)
            top_suggestions=["LUNG CANCER", "Lung Cancer", "lung cancer"],
            pv_set=pv_set,
        )

        # Then: Uses first valid alternative from suggestions
        assert result.is_conformant is True
        assert result.adjusted_value == "Lung Cancer"
        assert result.adjustment_source == AdjustmentSource.TOP_SUGGESTIONS

    def test_all_invalid_returns_non_conformant(self) -> None:
        """When nothing is valid, mark as non-conformant."""
        # Given: Nothing matches PV set
        pv_set = frozenset(["Lung Cancer", "breast cancer"])

        # When: Computing adjustment
        result = compute_pv_adjustment(
            original_value="unknown disease",
            top_harmonization="Unknown Disease",
            top_suggestions=["Unknown Disease", "UNKNOWN"],
            pv_set=pv_set,
        )

        # Then: Non-conformant, no adjustment
        assert result.is_conformant is False
        assert result.adjusted_value is None
        assert result.adjustment_source is None


class TestPVOverrideWhitespaceSensitivity:
    """Whitespace differences are semantically significant per domain rules."""

    def test_trailing_whitespace_triggers_override(self) -> None:
        """Original with trailing space is different from AI without."""
        # Given: Original has trailing space, AI doesn't
        pv_set = frozenset(["Lung Cancer ", "Lung Cancer"])  # Both are valid PVs

        # When: Original "Lung Cancer " (with space) vs AI "Lung Cancer"
        result = compute_pv_adjustment(
            original_value="Lung Cancer ",
            top_harmonization="Lung Cancer",
            top_suggestions=["Lung Cancer"],
            pv_set=pv_set,
        )

        # Then: Keeps original (with trailing space)
        assert result.is_conformant is True
        assert result.adjusted_value == "Lung Cancer "
        assert result.adjustment_source == AdjustmentSource.PV_OVERRIDE

    def test_case_difference_triggers_override(self) -> None:
        """Case differences are significant - original lowercase kept if valid."""
        # Given: Both cases are valid PVs
        pv_set = frozenset(["lung cancer", "Lung Cancer"])

        # When: Original is lowercase, AI suggests title case
        result = compute_pv_adjustment(
            original_value="lung cancer",
            top_harmonization="Lung Cancer",
            top_suggestions=["Lung Cancer"],
            pv_set=pv_set,
        )

        # Then: Keeps original lowercase
        assert result.adjusted_value == "lung cancer"
        assert result.adjustment_source == AdjustmentSource.PV_OVERRIDE


class TestPVOverrideEdgeCases:
    """Edge cases for the PV override logic."""

    def test_empty_original_not_in_pv_set(self) -> None:
        """Empty string original falls through to normal logic."""
        # Given: Empty original, AI suggests valid value
        pv_set = frozenset(["Lung Cancer", "breast cancer"])

        # When: Original is empty
        result = compute_pv_adjustment(
            original_value="",
            top_harmonization="Lung Cancer",
            top_suggestions=["Lung Cancer"],
            pv_set=pv_set,
        )

        # Then: Uses AI suggestion (empty string not in PV set)
        assert result.is_conformant is True
        assert result.adjusted_value is None

    def test_empty_pv_set_falls_through(self) -> None:
        """Empty PV set means nothing is conformant."""
        # Given: Empty PV set
        pv_set: frozenset[str] = frozenset()

        # When: Any values
        result = compute_pv_adjustment(
            original_value="anything",
            top_harmonization="Anything",
            top_suggestions=["Anything"],
            pv_set=pv_set,
        )

        # Then: Non-conformant (nothing matches empty set)
        assert result.is_conformant is False
        assert result.adjustment_source is None

    def test_empty_top_suggestions_with_invalid_ai(self) -> None:
        """Empty top_suggestions falls through to non-conformant when AI is also invalid."""
        # Given: No alternative suggestions, and AI is not in PV set
        pv_set = frozenset(["Lung Cancer", "breast cancer"])

        # When: Empty suggestions list
        result = compute_pv_adjustment(
            original_value="unknown disease",
            top_harmonization="Unknown Disease",
            top_suggestions=[],
            pv_set=pv_set,
        )

        # Then: Non-conformant (no valid alternatives to try)
        assert result.is_conformant is False
        assert result.adjusted_value is None
        assert result.adjustment_source is None
