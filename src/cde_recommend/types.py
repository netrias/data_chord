"""Domain types for CDE recommendation. Changes when the data model or API contract changes."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Closed set of inferred column types. Adding a new type here is a breaking
# change — downstream sampling logic in profiler._sample_values branches on
# specific values. The matcher no longer branches on specific values; all
# columns flow through the same LLM path regardless of dtype.
ColumnType = Literal["numeric", "id_like", "categorical", "free_text", "mixed"]
ColumnErrorCode = Literal["matching_failed", "rate_limited"]


@dataclass(frozen=True)
class CDE:
    """A Common Data Element loaded from the catalog.

    ``pv_values`` is a tuple (not list) so the dataclass stays hashable and
    safe to share across the warm-start cache without defensive copying.
    """

    cde_id: int | None
    cde_key: str
    pv_values: tuple[str, ...]


@dataclass(frozen=True)
class ColumnProfile:
    """Type-inferred view of a source column used to build the LLM user message."""

    column_name: str
    dtype: ColumnType
    sample_values: list[str]


class Harmonization(StrEnum):
    """Describes what happens to the column's values at harmonize time for one match.

    Wire form is the string value (StrEnum.value equals the member name lowercased-
    with-underscores). Consumers treat unknown values conservatively as "not
    harmonizable" so new variants can be added without breaking old clients.
    """

    # Target has non-empty PVs and the column's dtype is compatible. Values will
    # be mapped to permissible values during harmonization.
    HARMONIZABLE = "harmonizable"
    # Target CDE has no active permissible values. Target name is recognized,
    # but there is nothing to constrain values against; they pass through.
    NO_PERMISSIBLE_VALUES = "no_permissible_values"
    # Column's profiled dtype is numeric. Regardless of target, we do not
    # harmonize numeric data today; values pass through unchanged.
    NUMERIC = "numeric"


@dataclass(frozen=True)
class CDEMatch:
    """A real, resolved link between a source column and a target CDE.

    Absence of a match is represented by an empty ``ColumnResult.matches`` list —
    never by a CDEMatch with a sentinel key. `cde_id` is always the persistent
    database id for the matched CDE.
    """

    cde_id: int | None
    cde_key: str
    rank: int
    confidence: float
    harmonization: Harmonization


@dataclass(frozen=True)
class ColumnError:
    """Per-column failure surfaced beside a neutral empty-match result.

    A failed column still has ``matches=[]`` so positional consumers can
    proceed safely. ``error`` distinguishes a real failure from a normal
    successful "no matching CDE" outcome.
    """

    code: ColumnErrorCode
    message: str


@dataclass(frozen=True)
class ColumnResult:
    """Outcome of evaluating one source column against the CDE catalog.

    column_type is the profiler's classification (numeric, id_like,
    categorical, free_text, mixed). Empty matches without error means the LLM
    found no match; empty matches with error means matching failed for that
    column and the caller should review or retry it.
    """

    column_name: str
    matches: list[CDEMatch]
    column_type: ColumnType
    error: ColumnError | None = None


@dataclass(frozen=True)
class ColumnInput:
    """A validated source column before recommendation profiling."""

    column_name: str
    column_values: list[str]


def compute_harmonization(column_type: ColumnType, cde: CDE) -> Harmonization:
    """Sole owner of the harmonization-status precedence rule.

    Column-level reasons beat target-level reasons: a numeric column cannot be
    harmonized regardless of target, a PV-less target cannot be harmonized
    regardless of column, otherwise harmonizable. Every match-construction path
    (exact match, LLM single-call, chunked final pass) routes through here so
    precedence is defined in exactly one place. Lives beside ``Harmonization``
    so there is no back-reference from the leaf matcher to the orchestration
    layer.
    """
    if column_type == "numeric":
        return Harmonization.NUMERIC
    if not cde.pv_values:
        return Harmonization.NO_PERMISSIBLE_VALUES
    return Harmonization.HARMONIZABLE


# --- Pydantic models for provider structured output ---


class PotentialMatchIndex(BaseModel):
    """Single entry in the model's response list.

    The model returns indices rather than CDE keys/ids so it cannot hallucinate
    values not in the candidate list — an out-of-range index is always
    detectable and a -1 index is an explicit no-match signal.
    """

    # `model_config = ConfigDict(...)` is Pydantic v2's per-model config hook —
    # it tunes validation/serialization behavior for one BaseModel subclass.
    # extra="forbid" translates to additionalProperties=false in the emitted
    # JSON schema, which tightens strict-mode enforcement against the
    # model hallucinating unexpected fields.
    model_config = ConfigDict(extra="forbid")

    candidate_index: int = Field(
        ge=-1,
        description="0-based index into candidate list; -1 => no match (omitted from results)",
    )
    rank: int = Field(
        ge=0,
        le=10,
        description=(
            "Advisory LLM-reported rank; rank=0 signals 'no good match' and "
            "drops the entry. Wire rank is positional, assigned by the pipeline."
        ),
    )
    confidence: float = Field(ge=0.0, le=1.0, description="0.0 (no match) to 1.0 (exact match)")


class ClosestMatchesIndex(BaseModel):
    """Top-level envelope for the model's structured output — wraps a list of matches.

    The provider tool contract requires a JSON object at the top level, not a
    bare array.
    """

    # See PotentialMatchIndex for the model_config / extra="forbid" rationale.
    model_config = ConfigDict(extra="forbid")

    closest_matches: list[PotentialMatchIndex] = Field(default_factory=list)
