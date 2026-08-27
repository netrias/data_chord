"""Application boundary for CDE recommendations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from src.domain.column_profile import ColumnProfile
from src.domain.columns import ColumnIdentity
from src.domain.manifest import ColumnMappingManifest
from src.domain.reference_data import ReferenceModel


class RecommendationUnavailableError(Exception):
    """The recommendation provider could not produce a usable result."""


@dataclass(frozen=True)
class ProfiledColumn:
    """One source column with stable identity and its bounded profile."""

    identity: ColumnIdentity
    profile: ColumnProfile

    def __post_init__(self) -> None:
        if self.identity.key != self.profile.column_key:
            raise ValueError("Profiled column identity does not match its profile")


class CdeRecommender(Protocol):
    """Recommend target CDEs without exposing a model provider to the app."""

    async def recommend(
        self,
        columns: Sequence[ProfiledColumn],
        reference_model: ReferenceModel,
        *,
        top_k: int = 5,
    ) -> ColumnMappingManifest: ...


__all__ = [
    "CdeRecommender",
    "ProfiledColumn",
    "RecommendationUnavailableError",
]
