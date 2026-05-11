"""Type classification for CDEs.

Axis of change: how a CDE's type is decided when the data model store does not
yet expose type metadata. Owns ``classify_cde`` — the single function that
assigns ``CdeType`` to a CDE. The data model adapter calls it twice: once when
wrapping SDK rows (PVs unknown) and once after PV sets resolve.

Invariant: the precedence order — override > known PV presence > heuristic —
is encoded here and nowhere else. Other layers read ``CDEInfo.cde_type`` and
trust it.
"""

from __future__ import annotations

from src.domain.cde import CdeType

# Team-curated list of CDE keys known to be numeric. Empty in v1; populate as
# the team encounters numeric CDEs in real data models.
NUMERIC_CDE_KEYS: frozenset[str] = frozenset()


def classify_cde(
    cde_key: str,
    has_pvs: bool | None,
    sample_is_numeric: bool,
) -> CdeType:
    """Resolve a CDE's type using override > PV presence > heuristic.

    ``has_pvs=None`` means PVs have not been fetched yet — used when the
    adapter wraps SDK rows initially. The adapter calls back with a concrete
    bool after PV lookup resolves.
    """
    if cde_key in NUMERIC_CDE_KEYS:
        return CdeType.NUMERIC
    if has_pvs is True:
        return CdeType.PV
    if has_pvs is False:
        return CdeType.PASSTHROUGH
    # PVs unknown: trust the column-data heuristic before defaulting to PV.
    if sample_is_numeric:
        return CdeType.NUMERIC
    return CdeType.PV
