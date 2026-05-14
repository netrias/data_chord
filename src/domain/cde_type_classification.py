"""Type classification for CDEs.

Axis of change: how a CDE's type is decided while the data model store does not
yet expose explicit type metadata. Other layers read ``CDEInfo.cde_type`` and
trust it.
"""

from __future__ import annotations

from src.domain.cde import CdeType


def classify_cde(
    has_pvs: bool | None,
) -> CdeType:
    """Resolve a CDE's type from known PV presence.

    ``has_pvs=None`` means PVs have not been fetched yet — used when the
    adapter wraps SDK rows initially. In that state we keep the conservative PV
    default until PV lookup confirms the CDE is passthrough.
    """
    if has_pvs is True:
        return CdeType.PV
    if has_pvs is False:
        return CdeType.PASSTHROUGH
    return CdeType.PV
