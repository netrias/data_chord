"""Stage 2 HTTP request/response schemas.

Defines the column-detail response consumed by the takeover modal: per-CDE
match counts, per-CDE type, PV overlap ratios, and the PV list for the
currently-selected CDE.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.domain.column_profile import ColumnProfilePayload


class CdeCatalogItem(BaseModel):
    """CDE option sent to the Stage 2 browser picker."""

    cde_id: int
    cde_key: str
    label: str
    description: str
    cde_type: str


class ColumnDetailResponse(BaseModel):
    """Combined payload for one column's takeover view.

    Bundling match counts + types + selected PVs into one round-trip avoids
    multiple sequential fetches when the takeover opens. The browser caches
    the response for the session.
    """

    column_key: str
    profile: ColumnProfilePayload | None = None
    match_counts: dict[str, int] = Field(default_factory=dict)
    overlap_by_cde: dict[str, float] = Field(default_factory=dict)
    cde_types: dict[str, str] = Field(default_factory=dict)
    selected_pvs: list[str] | None = None
