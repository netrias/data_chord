"""Type classification for CDEs.

Axis of change: how a CDE's type is decided while the data model store does not
yet expose explicit type metadata. Other layers read ``CDEInfo.cde_type`` and
trust it.
"""

from __future__ import annotations

from src.domain.cde import CDEInfo, CdeType
from src.domain.cde_catalog import CdeCatalog
from src.domain.cde_pv_catalog import CdePvCatalog


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


def refine_cde_types_from_pvs(
    catalog: CdeCatalog,
    pv_sets: CdePvCatalog,
) -> CdeCatalog:
    """Return CDE types refined by PV sets that were fetched successfully."""
    refined: list[CDEInfo] = []
    for cde in catalog:
        if not pv_sets.has(cde.cde_key):
            refined.append(cde)
            continue
        cde_type = classify_cde(has_pvs=bool(pv_sets.get(cde.cde_key)))
        if cde_type == cde.cde_type:
            refined.append(cde)
            continue
        refined.append(
            CDEInfo(
                cde_id=cde.cde_id,
                cde_key=cde.cde_key,
                description=cde.description,
                cde_type=cde_type,
            )
        )
    return CdeCatalog.from_cdes(refined)
