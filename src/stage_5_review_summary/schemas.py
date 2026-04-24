"""Response schemas for the Stage 5 summary API.

Axis of change: the shape of the data the Stage 5 UI consumes when rendering
per-term lineage and per-column transformation counts. Models are derived from
the manifest; they are not the canonical store of any concept.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.domain.schemas import FILE_ID_MIN_LENGTH, FILE_ID_PATTERN


class StageFiveRequest(BaseModel):
    """Identifies which upload's manifest to summarize.

    Stage 5 is stateless — the file_id is the only input and everything else
    is derived from the persisted manifest and session cache.
    """

    file_id: str = Field(..., min_length=FILE_ID_MIN_LENGTH, pattern=FILE_ID_PATTERN)


class ColumnSummary(BaseModel):
    """Per-column rollup of transformation outcomes.

    Precomputed server-side so the UI can show "X AI changes, Y manual,
    Z unchanged" without rescanning term rows client-side.
    """

    column: str
    distinct_terms: int
    ai_changes: int
    manual_changes: int
    unchanged: int


class TransformationStep(BaseModel):
    """One entry in a term's lineage (original → AI → user overrides).

    `source` drives UI badging ("original" / "ai" / "user"); `is_pv_conformant`
    is precomputed server-side because the PV set lives in the session cache
    and is not shipped to the browser.
    """

    value: str
    source: str
    timestamp: str | None = None
    user_id: str | None = None
    is_pv_conformant: bool = True


class TermMapping(BaseModel):
    """A single source term and its full transformation history.

    One mapping per (column, distinct original value) — duplicates in the
    source CSV are collapsed so the UI shows each term once with its lineage.
    """

    column: str
    original_value: str
    final_value: str
    is_pv_conformant: bool = True
    history: list[TransformationStep] = Field(default_factory=list)


class StageFiveSummaryResponse(BaseModel):
    """Full payload for the Stage 5 review page.

    `non_conformant_count` is precomputed so the header badge renders without
    the client iterating `term_mappings` — matters for large uploads.
    """

    column_summaries: list[ColumnSummary]
    term_mappings: list[TermMapping]
    non_conformant_count: int = 0


__all__ = [
    "ColumnSummary",
    "StageFiveRequest",
    "StageFiveSummaryResponse",
    "TermMapping",
    "TransformationStep",
]
