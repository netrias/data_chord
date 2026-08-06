"""Unit tests for PV override protection.

Validates that valid original values are never replaced by AI harmonization,
even when the AI suggestion is also a valid PV.
"""

from __future__ import annotations

from src.domain.pv_validation import compute_pv_adjustment


class TestOriginalValueProtection:
    """Original values in PV set should be preserved, not replaced by AI."""

    def test_original_valid_ai_different_preserves_original(self) -> None:
        """When original is valid but AI suggests different value, preserve the original."""
        # Given: Original "lung cancer" is valid, AI suggests "Lung Cancer"
        pv_set = frozenset(["lung cancer", "Lung Cancer", "breast cancer"])

        # When: Computing adjustment
        result = compute_pv_adjustment(
            original_value="lung cancer",
            top_harmonization="Lung Cancer",
            top_suggestions=["Lung Cancer", "LUNG CANCER"],
            pv_set=pv_set,
        )

        # Then: Reverts to the original value
        assert result == "lung cancer"

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
        assert result is None

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
        assert result is None

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
        assert result == "Lung Cancer"

    def test_all_invalid_values_produce_no_replacement(self) -> None:
        """When nothing is valid, leave the provider value for later review."""
        # Given: Nothing matches PV set
        pv_set = frozenset(["Lung Cancer", "breast cancer"])

        # When: Computing adjustment
        result = compute_pv_adjustment(
            original_value="unknown disease",
            top_harmonization="Unknown Disease",
            top_suggestions=["Unknown Disease", "UNKNOWN"],
            pv_set=pv_set,
        )

        # Then: no valid replacement is available
        assert result is None


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
        assert result == "Lung Cancer "

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
        assert result == "lung cancer"


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
        assert result is None

    def test_empty_pv_set_produces_no_replacement(self) -> None:
        """An empty PV set cannot supply a replacement."""
        # Given: Empty PV set
        pv_set: frozenset[str] = frozenset()

        # When: Any values
        result = compute_pv_adjustment(
            original_value="anything",
            top_harmonization="Anything",
            top_suggestions=["Anything"],
            pv_set=pv_set,
        )

        # Then: nothing matches the empty set
        assert result is None

    def test_empty_top_suggestions_produce_no_replacement(self) -> None:
        """No replacement is available when the provider value is invalid."""
        # Given: No alternative suggestions, and AI is not in PV set
        pv_set = frozenset(["Lung Cancer", "breast cancer"])

        # When: Empty suggestions list
        result = compute_pv_adjustment(
            original_value="unknown disease",
            top_harmonization="Unknown Disease",
            top_suggestions=[],
            pv_set=pv_set,
        )

        # Then: there are no alternatives to try
        assert result is None
