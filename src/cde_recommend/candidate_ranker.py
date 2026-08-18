"""Model-neutral boundary for ranking CDE candidates."""

from typing import Protocol

from src.cde_recommend.types import PotentialMatchIndex


class CandidateRanker(Protocol):
    """Rank one source-column prompt against the supplied CDE prompt."""

    async def rank(
        self,
        developer_message: str,
        user_message: str,
    ) -> list[PotentialMatchIndex]: ...


__all__ = ["CandidateRanker"]
