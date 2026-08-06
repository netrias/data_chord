"""
Pure functions for validating values against permissible value sets.

Validation logic is kept pure (no I/O) to enable testing without mocks.
"""

def compute_pv_adjustment(
    original_value: str,
    top_harmonization: str,
    top_suggestions: list[str],
    pv_set: frozenset[str],
) -> str | None:
    """Return a PV-safe replacement, or None when the provider value stays.

    A valid original value takes priority over a different provider value. If
    neither the provider value nor a suggestion is valid, later review stages
    report the provider value as non-conformant instead of inventing a value.
    """
    if original_value in pv_set:
        return original_value if original_value != top_harmonization else None
    if top_harmonization in pv_set:
        return None
    return next((suggestion for suggestion in top_suggestions if suggestion in pv_set), None)


def check_value_conformance(
    value: str | None,
    pv_set: frozenset[str] | None,
) -> bool:
    """Assume conformant when PVs unavailable (graceful degradation).

    None/empty values are conformant since they represent missing data, not invalid data.
    """
    if pv_set is None or not pv_set:
        return True
    if value is None or value == "":
        return True
    return value in pv_set
