"""Type classification for CDEs.

Axis of change: how a CDE's type is decided while the data model store does not
yet expose explicit type metadata. Other layers read ``CDEInfo.cde_type`` and
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
) -> CdeType:
    """Resolve a CDE's type using override > known PV presence > PV default.

    ``has_pvs=None`` means PVs have not been fetched yet — used when the
    adapter wraps SDK rows initially. In that state we keep the conservative PV
    default until PV lookup confirms the CDE is passthrough.
    """
    if cde_key in NUMERIC_CDE_KEYS:
        return CdeType.NUMERIC
    if has_pvs is True:
        return CdeType.PV
    if has_pvs is False:
        return CdeType.PASSTHROUGH
    return CdeType.PV
