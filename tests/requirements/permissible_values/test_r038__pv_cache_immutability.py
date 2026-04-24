"""Requirement tests for non-user-visible PV cache invariants."""

from __future__ import annotations

import pytest

from src.domain.data_model_cache import SessionCache
from tests.cache_helpers import cache_pvs_for_cde, set_cache_pvs
from tests.requirements.helpers import CANONICAL_DIAGNOSIS, PRIMARY_DIAGNOSIS_CDE


@pytest.mark.requirements("R-038", "R-075")
def test_r038_r075__pv_cache_stores_values_as_immutable_lookup_sets() -> None:
    """
    Given: PVs are loaded into a session cache.
    When: A caller retrieves the PV set for a CDE.
    Then: The PVs are available as an immutable lookup set.
    """
    # Given
    cache = SessionCache()
    mutable_source = [CANONICAL_DIAGNOSIS]
    assert isinstance(mutable_source, list)

    # When
    set_cache_pvs(cache, PRIMARY_DIAGNOSIS_CDE, frozenset(mutable_source))
    pvs = cache_pvs_for_cde(cache, PRIMARY_DIAGNOSIS_CDE)

    # Then
    assert isinstance(pvs, frozenset)
    assert CANONICAL_DIAGNOSIS in pvs
